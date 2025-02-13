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
from pydantic import BaseSettings

from supa.connection.error import GenericRmError
from supa.job.shared import NsiException
from supa.nrm.backend import STP, BaseBackend
from supa.util.find import find_file


class BackendSettings(BaseSettings):
    """Backend settings with default values.

    See also: the ``src/supa/nrm/backends/example.env`` file
    """

    stps_config: str = "stps_config.yml"


class Backend(BaseBackend):
    """Topology Test backend interface.

    Test topology() function.
    """

    def __init__(self) -> None:
        """Load properties from 'yaml_topology_test.env'."""
        super(Backend, self).__init__()
        file_basename = os.path.basename(__file__).split('.')[0]
        self.configs_dir = file_basename + "_configs"
        self.backend_settings = BackendSettings(_env_file=(env_file := find_file(self.configs_dir + "/" + file_basename + ".env")))
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
        """Activate resources."""
        self.log.info(
            "Activate resources in yaml_topology_test NRM", backend=self.__module__, primitive="activate", connection_id=str(connection_id)
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
        """Deactivate resources."""
        self.log.info(
            "Deactivate resources in yaml_topology_test NRM", backend=self.__module__, primitive="deactivate", connection_id=str(connection_id)
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
        """Read STPs from yaml file."""
        self.log.info("get topology from yaml_topology_test NRM", backend=self.__module__, primitive="topology")

        stp_list_file = find_file(self.configs_dir + "/" + self.backend_settings.stps_config)
        self.log.info("Read STPs config", path=str(stp_list_file))

        def _load_stp_from_file(stp_list_file: str) -> List[STP]:
            with open(stp_list_file, "r") as stps_file:
                stp_list = [STP(**stp) for stp in yaml.safe_load(stps_file)["stps"]]
            return stp_list

        stp_list = _load_stp_from_file(stp_list_file)
        self.log.info("STP list", stp_list=stp_list)

        return stp_list


### Not implemented functions, just provide logging. ###

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
            "Reserve resources in yaml_topology_test NRM", backend=self.__module__, primitive="reserve", connection_id=str(connection_id)
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
            "Reserve timeout resources in yaml_topology_test NRM",
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
            "Reserve commit resources in yaml_topology_test NRM",
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
            "Reserve abort resources in yaml_topology_test NRM",
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
            "Provision resources in yaml_topology_test NRM", backend=self.__module__, primitive="provision", connection_id=str(connection_id)
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
            "Release resources in yaml_topology_test NRM", backend=self.__module__, primitive="release", connection_id=str(connection_id)
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
            "Terminate resources in yaml_topology_test NRM", backend=self.__module__, primitive="terminate", connection_id=str(connection_id)
        )
        return None
