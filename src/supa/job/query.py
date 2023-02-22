#  Copyright 2023 SURF.
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
from __future__ import annotations

from typing import List, Type
from uuid import UUID

import structlog
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import func, or_
from structlog.stdlib import BoundLogger

from supa.connection import requester
from supa.connection.fsm import DataPlaneStateMachine, ReservationStateMachine
from supa.db.model import Notification, Reservation, Result
from supa.grpc_nsi.connection_common_pb2 import Header
from supa.grpc_nsi.connection_provider_pb2 import QueryNotificationRequest, QueryRequest
from supa.grpc_nsi.connection_requester_pb2 import (
    DataPlaneStateChangeRequest,
    ErrorEventRequest,
    MessageDeliveryTimeoutRequest,
    QueryConfirmedRequest,
    QueryNotificationConfirmedRequest,
    QueryResult,
    ReserveTimeoutRequest,
)
from supa.job.shared import Job
from supa.util.converter import to_connection_states, to_criteria
from supa.util.timestamp import as_utc_timestamp

logger = structlog.get_logger(__name__)


def create_query_confirmed_request(
    pb_query_request: QueryRequest,
) -> QueryConfirmedRequest:
    """Create a list of reservation information matching the request.

    Args:
        pb_query_request: Query request with match criteria.

    Returns:
        List of reservation information.
    """
    from supa.db.session import db_session

    with db_session() as session:
        or_filter = []
        if pb_query_request.connection_id:
            or_filter += [
                Reservation.connection_id == UUID(str(connection_id))
                for connection_id in pb_query_request.connection_id
            ]
        if pb_query_request.global_reservation_id:
            or_filter += [
                Reservation.global_reservation_id == global_reservation_id
                for global_reservation_id in pb_query_request.global_reservation_id
            ]
        reservations: List[Reservation] = (
            session.query(Reservation)
            .filter(or_(*or_filter))
            .filter(Reservation.reservation_state != ReservationStateMachine.ReserveChecking.value)
            .filter(Reservation.reservation_state != ReservationStateMachine.ReserveFailed.value)
            .filter(Reservation.last_modified > as_utc_timestamp(pb_query_request.if_modified_since))
            .all()
        )
        last_modified = session.query(func.max(Reservation.last_modified)).scalar()

        header = Header()
        header.CopyFrom(pb_query_request.header)
        request = QueryConfirmedRequest(header=header)
        request.last_modified.FromDatetime(last_modified)
        for reservation in reservations:
            query_result = QueryResult()
            query_result.connection_id = str(reservation.connection_id)
            query_result.requester_nsa = reservation.requester_nsa
            query_result.connection_states.CopyFrom(
                to_connection_states(
                    reservation,
                    data_plane_active=reservation.data_plane_state == DataPlaneStateMachine.Activated.value,
                )
            )
            if reservation.global_reservation_id:
                query_result.global_reservation_id = reservation.global_reservation_id
            if reservation.description:
                query_result.description = reservation.description
            # TODO: when Modify Reservation is implemented, add all criteria
            query_result.criteria.append(to_criteria(reservation))
            # TODO: implement notification_id and result_id
            max_notification_id = session.query(
                func.max(Notification.notification_id).filter(Notification.connection_id == reservation.connection_id)
            ).scalar()
            query_result.notification_id = max_notification_id if max_notification_id else 0
            max_result_id = session.query(
                func.max(Result.result_id).filter(Result.connection_id == reservation.connection_id)
            ).scalar()
            query_result.result_id = max_result_id if max_result_id else 0
            request.reservation.append(query_result)

        return request


def create_query_notification_confirmed_request(
    pb_query_notification_request: QueryNotificationRequest,
) -> QueryNotificationConfirmedRequest:
    """Get a list of notifications for connection ID supplied by query notification request.

    Query notification(s) of requested connection ID, if any,
    optionally limiting the notifications by start and end notification ID.

    Args:
        pb_query_notification_request (QueryNotificationRequest):

    Returns:
        QueryNotificationConfirmedRequest with list of notifications.
    """
    from supa.db.session import db_session

    with db_session() as session:
        query = session.query(Notification).filter(
            Notification.connection_id == UUID(pb_query_notification_request.connection_id)
        )
        if pb_query_notification_request.start_notification_id > 0:
            query = query.filter(Notification.notification_id >= pb_query_notification_request.start_notification_id)
        if pb_query_notification_request.end_notification_id > 0:
            query = query.filter(Notification.notification_id <= pb_query_notification_request.end_notification_id)
        notifications: List[Notification] = query.all()

        header = Header()
        header.CopyFrom(pb_query_notification_request.header)
        request = QueryNotificationConfirmedRequest(header=header)
        for notification in notifications:
            if notification.notification_type == "ReserveTimeoutRequest":
                request.reserve_timeout.append(ReserveTimeoutRequest().FromString(notification.notification_data))
            elif notification.notification_type == "ErrorEventRequest":
                request.error_event.append(ErrorEventRequest().FromString(notification.notification_data))
            elif notification.notification_type == "MessageDeliveryTimeoutRequest":
                request.message_delivery_timeout.append(
                    MessageDeliveryTimeoutRequest().FromString(notification.notification_data)
                )
            elif notification.notification_type == "DataPlaneStateChangeRequest":
                request.data_plane_state_change.append(
                    DataPlaneStateChangeRequest().FromString(notification.notification_data)
                )
            else:
                logger.error("unknown notification type: %s" % notification.notification_type)

        return request


class QuerySummaryJob(Job):
    """Handle query summary requests."""

    log: BoundLogger
    pb_query_request: QueryRequest

    def __init__(self, pb_query_request: QueryRequest):
        """Initialize the QuerySummaryJob.

        Args:
           pb_query_request: protobuf query request message

                Elements compose a filter for specifying the reservations to return
                in response to the query operation. Supports the querying of reservations
                based on connectionId or globalReservationId. Filter items specified
                are OR'ed to build the match criteria. If no criteria are specified
                then all reservations associated with the requesting NSA are returned.

                Elements:

                connectionId - Return reservations containing this connectionId.

                globalReservationId - Return reservations containing this globalReservationId.

                ifModifiedSince - If an NSA receives a querySummary or querySummarySync
                message containing this element, then the NSA only returns those
                reservations matching the filter elements (connectionId,
                globalReservationId) if the reservation has been created, modified, or
                has undergone a change since the specified ifModifiedSince time.
        """
        self.log = logger.bind(
            job="QuerySummaryJob",
            connection_ids=pb_query_request.connection_id,
            global_reservation_ids=pb_query_request.global_reservation_id,
            if_modified_since=as_utc_timestamp(pb_query_request.if_modified_since).isoformat(),
        )
        self.pb_query_request = pb_query_request

    def __call__(self) -> None:
        """Query summary request.

        Query summary listing reservations matching the optional connection id(s),
        global reservation id(s) and if modified since timestamp.
        """
        self.log.info("Query summary")
        request = create_query_confirmed_request(self.pb_query_request)
        stub = requester.get_stub()
        self.log.debug("Sending message", method="QuerySummaryConfirmed", request_message=request)
        stub.QuerySummaryConfirmed(request)

    @classmethod
    def recover(cls: Type[QuerySummaryJob]) -> List[Job]:
        """Recover QuerySummaryJob's that did not get to run before SuPA was terminated.

        As no query summary request details are stored in the database (at this time),
        it is not possible to recover QuerySummaryJob's.

        Returns:
            List of QuerySummaryJob's that still need to be run (currently always empty List).
        """
        return []

    def trigger(self) -> DateTrigger:
        """Trigger for QuerySummaryJob's.

        Returns:
            DateTrigger set to None, which means run now.
        """
        return DateTrigger(run_date=None)  # Run immediately


class QueryRecursiveJob(Job):
    """Handle query recursive requests."""

    log: BoundLogger
    pb_query_request: QueryRequest

    def __init__(self, pb_query_request: QueryRequest):
        """Initialize the QueryRecursiveJob.

        Args:
           pb_query_request: protobuf query request message

                Elements compose a filter for specifying the reservations to return
                in response to the query operation. Supports the querying of reservations
                based on connectionId or globalReservationId. Filter items specified
                are OR'ed to build the match criteria. If no criteria are specified
                then all reservations associated with the requesting NSA are returned.

                Elements:

                connectionId - Return reservations containing this connectionId.

                globalReservationId - Return reservations containing this globalReservationId.

                ifModifiedSince - If an NSA receives a querySummary or querySummarySync
                message containing this element, then the NSA only returns those
                reservations matching the filter elements (connectionId,
                globalReservationId) if the reservation has been created, modified, or
                has undergone a change since the specified ifModifiedSince time.
        """
        self.log = logger.bind(
            job="QueryRecursiveJob",
            connection_ids=pb_query_request.connection_id,
            global_reservation_ids=pb_query_request.global_reservation_id,
            if_modified_since=as_utc_timestamp(pb_query_request.if_modified_since).isoformat(),
        )
        self.pb_query_request = pb_query_request

    def __call__(self) -> None:
        """Query recursive request.

        Query recursive listing reservations matching the optional connection id(s),
        global reservation id(s) and if modified since timestamp.
        """
        self.log.info("Query recursive")
        request = create_query_confirmed_request(self.pb_query_request)
        stub = requester.get_stub()
        self.log.debug("Sending message", method="QueryRecursiveConfirmed", request_message=request)
        stub.QueryRecursiveConfirmed(request)

    @classmethod
    def recover(cls: Type[QueryRecursiveJob]) -> List[Job]:
        """Recover QueryRecursiveJob's that did not get to run before SuPA was terminated.

        As no query recursive request details are stored in the database (at this time),
        it is not possible to recover QueryRecursiveJob's.

        Returns:
            List of QueryRecursiveJob's that still need to be run (currently always empty List).
        """
        return []

    def trigger(self) -> DateTrigger:
        """Trigger for QueryRecursiveJob's.

        Returns:
            DateTrigger set to None, which means run now.
        """
        return DateTrigger(run_date=None)  # Run immediately


class QueryNotificationJob(Job):
    """Handle query recursive requests."""

    log: BoundLogger
    pb_query_notification_request: QueryNotificationRequest

    def __init__(self, pb_query_notification_request: QueryNotificationRequest):
        """Initialize the QueryNotificationJob.

        The QueryNotification message provides a
        mechanism for a Requester NSA to query a Provider NSA for a
        set of notifications against a specific connectionId.

        Args:
           pb_query_notification_request: protobuf query notification request message

                Elements compose a filter for specifying the notifications to
                return in response to the query operation.  The filter query
                provides an inclusive range of notification identifiers based
                on connectionId.

                Elements:

                connectionId - Notifications for this connectionId.

                startNotificationId - The start of the range of notificationIds
                to return.  If not present then the query should start from
                oldest notificationId available.

                endNotificationId - The end of the range of notificationIds
                to return.  If not present then the query should end with
                the newest notificationId available.
        """
        self.log = logger.bind(
            job="QueryNotificationJob",
            connection_id=pb_query_notification_request.connection_id,
            start_notification_id=pb_query_notification_request.start_notification_id,
            end_notification_id=pb_query_notification_request.end_notification_id,
        )
        self.pb_query_notification_request = pb_query_notification_request

    def __call__(self) -> None:
        """Query notification request.

        Query notification(s) of requested connection ID, if any,
        optionally limiting the notifications by start and end notification ID.
        """
        self.log.info("Query notification")
        request = create_query_notification_confirmed_request(self.pb_query_notification_request)
        stub = requester.get_stub()
        self.log.debug("Sending message", method="QueryNotificationConfirmed", request_message=request)
        stub.QueryNotificationConfirmed(request)

    @classmethod
    def recover(cls: Type[QueryNotificationJob]) -> List[Job]:
        """Recover QueryNotificationJob's that did not get to run before SuPA was terminated.

        As no query notification request details are stored in the database (at this time),
        it is not possible to recover QueryNotificationJob's.

        Returns:
            List of QueryNotificationJob's that still need to be run (currently always empty List).
        """
        return []

    def trigger(self) -> DateTrigger:
        """Trigger for QueryNotificationJob's.

        Returns:
            DateTrigger set to None, which means run now.
        """
        return DateTrigger(run_date=None)  # Run immediately
