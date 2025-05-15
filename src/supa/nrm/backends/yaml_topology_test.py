#  Copyright 2022 SURF.
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

import os
from typing import List, Optional
from uuid import UUID, uuid4

import yaml
from pydantic_settings import BaseSettings

from supa.connection.error import GenericRmError
from supa.job.shared import NsiException
from supa.nrm.backend import STP, BaseBackend
from supa.util.find import find_file


class BackendSettings(BaseSettings):
    """Backend settings with default values.

    See also: the ``src/supa/nrm/backends/example.env`` file
    """

    # Configuration files
    stps_config: str = "stps_config.yml"


class Backend(BaseBackend):
    """Topology Test backend interface.

    Test topology() function.
    """

    def __init__(self) -> None:
        """Load backend properties from env file."""
        super(Backend, self).__init__()

        # Get backend name from the filename to make code more portable
        self.backend_name = os.path.basename(__file__).split(".")[0]
        self.configs_dir = f"{self.backend_name}_configs"

        # Load backend settings from environment file
        env_file = find_file(f"{self.configs_dir}/{self.backend_name}.env")
        self.backend_settings = BackendSettings(_env_file=env_file)
        self.log.info("Read backend properties", path=str(env_file))

    def activate(
        self,
        connection_id: UUID,
        bandwidth: int,
        src_port_id: str,
        src_vlan: int,
        dst_port_id: str,
        dst_vlan: int,
        circuit_id: str,
    ) -> Optional[str]:
        """Activate resources by configuring VLANs on the switch.

        Args:
            connection_id: Unique identifier for the connection
            bandwidth: Required bandwidth in Mbps
            src_port_id: Source port identifier
            src_vlan: Source VLAN number
            dst_port_id: Destination port identifier
            dst_vlan: Destination VLAN number
            circuit_id: Circuit identifier

        Returns:
            Circuit identifier (generated if not provided)

        Raises:
            NsiException: If VLANs don't match
        """
        self.log.info(
            "Activate resources in {0} NRM".format(self.backend_name),
            backend=self.__module__,
            primitive="activate",
            connection_id=str(connection_id),
        )

        if not src_vlan == dst_vlan:
            raise NsiException(GenericRmError, "VLANs must match")
        circuit_id = uuid4().urn  # dummy circuit id
        self.log.info(
            "Link up",
            src_port_id=src_port_id,
            dst_port_id=dst_port_id,
            src_vlan=src_vlan,
            dst_vlan=dst_vlan,
            circuit_id=circuit_id,
        )
        return circuit_id

    def deactivate(
        self,
        connection_id: UUID,
        bandwidth: int,
        src_port_id: str,
        src_vlan: int,
        dst_port_id: str,
        dst_vlan: int,
        circuit_id: str,
    ) -> Optional[str]:
        """Deactivate resources by removing VLANs from the switch.

        Args:
            connection_id: Unique identifier for the connection
            bandwidth: Required bandwidth in Mbps
            src_port_id: Source port identifier
            src_vlan: Source VLAN number
            dst_port_id: Destination port identifier
            dst_vlan: Destination VLAN number
            circuit_id: Circuit identifier
        """
        self.log.info(
            "Deactivate resources in {0} NRM".format(self.backend_name),
            backend=self.__module__,
            primitive="deactivate",
            connection_id=str(connection_id),
        )

        self.log.info(
            "Link down",
            src_port_id=src_port_id,
            dst_port_id=dst_port_id,
            src_vlan=src_vlan,
            dst_vlan=dst_vlan,
            circuit_id=circuit_id,
        )
        return None

    def topology(self) -> List[STP]:
        """Read STPs from YAML file and convert to STP objects."""
        self.log.info(
            "Get topology from {0} NRM".format(self.backend_name), backend=self.__module__, primitive="topology"
        )

        # Find and load the STP configuration file
        config_path = find_file(f"{self.configs_dir}/{self.backend_settings.stps_config}")

        # Load and process STPs
        with open(config_path, "r") as f:
            stp_configs = yaml.safe_load(f)["stps"]
            return [self._process_stp_config(config) for config in stp_configs]

    def _process_stp_config(self, config: dict) -> STP:
        """Convert a single STP configuration to an STP object.

        Handles both bidirectional and directional STP configurations.

        Args:
            config: STP configuration dictionary

        Returns:
            STP object
        """
        # Create a copy to avoid modifying the original
        processed = config.copy()

        # Process bidirectional STP configuration
        if "remote_stp" in processed:
            remote = processed.pop("remote_stp")
            prefix = remote["prefix_urn"]
            stp_id = remote["id"]

            processed["is_alias_in"] = f"{prefix}:{stp_id}:out"
            processed["is_alias_out"] = f"{prefix}:{stp_id}:in"

        # Process directional in/out configurations
        if "remote_stp_in" in processed:
            remote = processed.pop("remote_stp_in")
            processed["is_alias_in"] = f"{remote['prefix_urn']}:{remote['id']}"

        if "remote_stp_out" in processed:
            remote = processed.pop("remote_stp_out")
            processed["is_alias_out"] = f"{remote['prefix_urn']}:{remote['id']}"

        return STP(**processed)

    # Not implemented functions, provide logging only

    def reserve(
        self,
        connection_id: UUID,
        bandwidth: int,
        src_port_id: str,
        src_vlan: int,
        dst_port_id: str,
        dst_vlan: int,
    ) -> Optional[str]:
        """Reserve resources in NRM."""
        self.log.info(
            "Reserve resources in {0} NRM".format(self.backend_name),
            backend=self.__module__,
            primitive="reserve",
            connection_id=str(connection_id),
        )
        return None

    def reserve_timeout(
        self,
        connection_id: UUID,
        bandwidth: int,
        src_port_id: str,
        src_vlan: int,
        dst_port_id: str,
        dst_vlan: int,
        circuit_id: str,
    ) -> Optional[str]:
        """Reserve timeout resources in NRM."""
        self.log.info(
            "Reserve timeout resources in {0} NRM".format(self.backend_name),
            backend=self.__module__,
            primitive="reserve_timeout",
            connection_id=str(connection_id),
        )
        return None

    def reserve_commit(
        self,
        connection_id: UUID,
        bandwidth: int,
        src_port_id: str,
        src_vlan: int,
        dst_port_id: str,
        dst_vlan: int,
        circuit_id: str,
    ) -> Optional[str]:
        """Reserve commit resources in NRM."""
        self.log.info(
            "Reserve commit resources in {0} NRM".format(self.backend_name),
            backend=self.__module__,
            primitive="reserve_commit",
            connection_id=str(connection_id),
        )
        return None

    def reserve_abort(
        self,
        connection_id: UUID,
        bandwidth: int,
        src_port_id: str,
        src_vlan: int,
        dst_port_id: str,
        dst_vlan: int,
        circuit_id: str,
    ) -> Optional[str]:
        """Reserve abort resources in NRM."""
        self.log.info(
            "Reserve abort resources in {0} NRM".format(self.backend_name),
            backend=self.__module__,
            primitive="reserve_abort",
            connection_id=str(connection_id),
        )
        return None

    def provision(
        self,
        connection_id: UUID,
        bandwidth: int,
        src_port_id: str,
        src_vlan: int,
        dst_port_id: str,
        dst_vlan: int,
        circuit_id: str,
    ) -> Optional[str]:
        """Provision resources in NRM."""
        self.log.info(
            "Provision resources in {0} NRM".format(self.backend_name),
            backend=self.__module__,
            primitive="provision",
            connection_id=str(connection_id),
        )
        return None

    def release(
        self,
        connection_id: UUID,
        bandwidth: int,
        src_port_id: str,
        src_vlan: int,
        dst_port_id: str,
        dst_vlan: int,
        circuit_id: str,
    ) -> Optional[str]:
        """Release resources in NRM."""
        self.log.info(
            "Release resources in {0} NRM".format(self.backend_name),
            backend=self.__module__,
            primitive="release",
            connection_id=str(connection_id),
        )
        return None

    def terminate(
        self,
        connection_id: UUID,
        bandwidth: int,
        src_port_id: str,
        src_vlan: int,
        dst_port_id: str,
        dst_vlan: int,
        circuit_id: str,
    ) -> Optional[str]:
        """Terminate resources in NRM."""
        self.log.info(
            "Terminate resources in {0} NRM".format(self.backend_name),
            backend=self.__module__,
            primitive="terminate",
            connection_id=str(connection_id),
        )
        return None
