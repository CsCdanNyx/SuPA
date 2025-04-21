#  Copyright 2023 ESnet / UCSD / SURF.
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
from netmiko import ConnectHandler
from pydantic import BaseSettings

from supa.connection.error import GenericRmError
from supa.job.shared import NsiException
from supa.nrm.backend import STP, BaseBackend
from supa.util.find import find_file


class BackendSettings(BaseSettings):
    """Backend settings with default values."""

    ssh_hostname: str = "localhost"
    ssh_port: int = 22
    ssh_host_fingerprint: str = ""
    ssh_username: str = ""
    ssh_password: str = ""
    ssh_private_key_path: str = ""
    ssh_public_key_path: str = ""

    stps_config: str = "stps_config.yml"

# parametrized commands
COMMAND_ENABLE = "enable"
COMMAND_CONFIGURE = "configure"
COMMAND_CREATE_VLAN = "interface vlan %i"
COMMAND_DELETE_VLAN = "no interface vlan %i"
COMMAND_INTERFACE = "interface ethernet %s"
COMMAND_MODE_ACCESS = "switchport mode access"
COMMAND_MODE_TRUNK = "switchport mode trunk"
COMMAND_ACCESS_VLAN = "switchport access vlan %i"
COMMAND_TRUNK_ADD_VLAN = "switchport trunk allowed vlan %i"
COMMAND_TRUNK_REM_VLAN = "no switchport trunk allowed vlan %i"
COMMAND_EXIT = "exit"
# COMMAND_COMMIT = "copy running-config startup-config"
COMMAND_COMMIT = "write"
COMMAND_NO_SHUTDOWN = "no shutdown"

# vlan and interface descriptions
COMMAND_VLAN_NAME = "description vlan-%i"  # Will use the VLAN number
COMMAND_INT_DESCRIPTION = "description port-%s"  # Will use the port identifier

def _create_configure_commands(source_port: str, dest_port: str, vlan: int) -> List[str]:
    createvlan = COMMAND_CREATE_VLAN % vlan
    intsrc = COMMAND_INTERFACE % source_port
    intdst = COMMAND_INTERFACE % dest_port
    modetrunk = COMMAND_MODE_TRUNK
    addvlan = COMMAND_TRUNK_ADD_VLAN % vlan
    cmdexit = COMMAND_EXIT
    vlanname = COMMAND_VLAN_NAME % vlan
    intsrc_desc = COMMAND_INT_DESCRIPTION % source_port
    intdst_desc = COMMAND_INT_DESCRIPTION % dest_port
    commands = [createvlan, vlanname, cmdexit, intsrc, intsrc_desc, modetrunk, addvlan, cmdexit, intdst, intdst_desc, modetrunk, addvlan, cmdexit]
    return commands


def _create_delete_commands(source_port: str, dest_port: str, vlan: int) -> List[str]:
    intsrc = COMMAND_INTERFACE % source_port
    intdst = COMMAND_INTERFACE % dest_port
    remvlan = COMMAND_TRUNK_REM_VLAN % vlan
    cmdexit = COMMAND_EXIT
    # deletevlan = COMMAND_DELETE_VLAN % vlan
    commands = [intsrc, remvlan, cmdexit, intdst, remvlan, cmdexit]  # , deletevlan]
    return commands


class Backend(BaseBackend):
    """Dell OS10 backend interface."""

    def __init__(self) -> None:
        """Load properties from 'dellOS10.env'."""
        super(Backend, self).__init__()
        file_basename = os.path.basename(__file__).split('.')[0]
        self.configs_dir = file_basename + "_configs"
        self.backend_settings = BackendSettings(_env_file=(env_file := find_file(self.configs_dir + "/" + file_basename + ".env")))
        self.log.info("Read backend properties", path=str(env_file))

        self.dell_os10_switch = {
            'device_type': 'dell_os10',
            'ip': self.backend_settings.ssh_hostname,
            'port': self.backend_settings.ssh_port,
            'username': self.backend_settings.ssh_username,
            'password': self.backend_settings.ssh_password,
            'use_keys': False,
            # 'session_log': 'session.log',
            'timeout': 30,
            'keepalive': 30,
        }


    def _check_ssh_pass_keys(self) -> None:
        privkey = None
        if self.backend_settings.ssh_private_key_path:
            if os.path.exists(self.backend_settings.ssh_private_key_path):
                privkey = self.backend_settings.ssh_private_key_path
            elif os.path.exists(os.path.expanduser(self.backend_settings.ssh_private_key_path)):
                privkey = os.path.expanduser(self.backend_settings.ssh_private_key_path)
            else:
                reason = "Incorrect private key path or file does not exist"
                self.log.warning("failed to initialise SSH client", reason=reason)
                raise NsiException(GenericRmError, reason)

        if privkey:
            self.dell_os10_switch["use_keys"] = True
            self.dell_os10_switch["key_file"] = privkey
        elif not self.backend_settings.ssh_password:
            raise AssertionError("No keys or password supplied")


    def _send_commands(self, commands: List[str]) -> None:
        self.log.debug("_send_commands() function with cli list: %r" % commands)

        try:
            self._check_ssh_pass_keys()
            self.log.debug("Send command start")
            with ConnectHandler(**self.dell_os10_switch) as conn:
                conn.enable()
                self.log.debug("Starting Config")
                conn.send_config_set(commands)
                self.log.debug("Finished configuration, saving config.")
                conn.send_command(COMMAND_COMMIT)

        except Exception as exception:
            self.log.warning("Error sending commands")
            raise NsiException(GenericRmError, "Error sending commands") from exception

        self.log.debug("Commands successfully committed")


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
            "Activate resources in dellOS10 NRM", backend=self.__module__, primitive="activate", connection_id=str(connection_id)
        )

        if not src_vlan == dst_vlan:
            raise NsiException(GenericRmError, "VLANs must match")
        self._send_commands(_create_configure_commands(src_port_id, dst_port_id, dst_vlan))
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
            "Deactivate resources in dellOS10 NRM", backend=self.__module__, primitive="deactivate", connection_id=str(connection_id)
        )

        self._send_commands(_create_delete_commands(src_port_id, dst_port_id, dst_vlan))
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
        self.log.info("get topology from dellOS10 NRM", backend=self.__module__, primitive="topology")

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
            "Reserve resources in dellOS10 NRM", backend=self.__module__, primitive="reserve", connection_id=str(connection_id)
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
            "Reserve timeout resources in dellOS10 NRM",
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
            "Reserve commit resources in dellOS10 NRM",
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
            "Reserve abort resources in dellOS10 NRM",
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
            "Provision resources in dellOS10 NRM", backend=self.__module__, primitive="provision", connection_id=str(connection_id)
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
            "Release resources in dellOS10 NRM", backend=self.__module__, primitive="release", connection_id=str(connection_id)
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
            "Terminate resources in dellOS10 NRM", backend=self.__module__, primitive="terminate", connection_id=str(connection_id)
        )
        return None
