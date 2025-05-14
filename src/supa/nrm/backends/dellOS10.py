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

    # SSH connection settings
    ssh_hostname: str = "localhost"
    ssh_port: int = 22
    ssh_host_fingerprint: str = ""
    ssh_username: str = ""
    ssh_password: str = ""
    ssh_private_key_path: str = ""
    ssh_public_key_path: str = ""

    # Configuration files
    stps_config: str = "stps_config.yml"

    # Switch type settings
    device_type: str = "dell_os10"

    # Command templates - these can be overridden in the env file for different switches
    cmd_enable: str = "enable"
    cmd_configure: str = "configure"
    cmd_create_vlan: str = "interface vlan %i"
    cmd_delete_vlan: str = "no interface vlan %i"
    cmd_interface: str = "interface ethernet %s"
    cmd_mode_access: str = "switchport mode access"
    cmd_mode_trunk: str = "switchport mode trunk"
    cmd_access_vlan: str = "switchport access vlan %i"
    cmd_trunk_add_vlan: str = "switchport trunk allowed vlan %i"
    cmd_trunk_rm_vlan: str = "no switchport trunk allowed vlan %i"
    cmd_exit: str = "exit"
    cmd_commit: str = "write"  # "copy running-config startup-config" on some platforms
    cmd_no_shutdown: str = "no shutdown"
    cmd_vlan_name: str = "description vlan-%i"
    cmd_int_description: str = "description port-%s"


class Backend(BaseBackend):
    """Network switch backend interface using Netmiko."""

    def __init__(self) -> None:
        """Load backend properties from env file."""
        super(Backend, self).__init__()

        # Get backend name from the filename to make code more portable
        self.backend_name = os.path.basename(__file__).split(".")[0]
        self.configs_dir = f"{self.backend_name}_configs"

        # Load backend settings from environment file
        env_file = find_file(f"src/supa/nrm/backends/{self.configs_dir}/{self.backend_name}.env")
        self.backend_settings = BackendSettings(_env_file=env_file)
        self.log.info("Read backend properties", path=str(env_file))

        # Configure switch connection settings
        self.switch_config = {
            "device_type": self.backend_settings.device_type,
            "ip": self.backend_settings.ssh_hostname,
            "port": self.backend_settings.ssh_port,
            "username": self.backend_settings.ssh_username,
            "password": self.backend_settings.ssh_password,
            "use_keys": False,
            "timeout": 30,
            "keepalive": 30,
        }

        # Store commands for easy access
        self.commands = {
            "enable": self.backend_settings.cmd_enable,
            "configure": self.backend_settings.cmd_configure,
            "create_vlan": self.backend_settings.cmd_create_vlan,
            "delete_vlan": self.backend_settings.cmd_delete_vlan,
            "interface": self.backend_settings.cmd_interface,
            "mode_access": self.backend_settings.cmd_mode_access,
            "mode_trunk": self.backend_settings.cmd_mode_trunk,
            "access_vlan": self.backend_settings.cmd_access_vlan,
            "trunk_add_vlan": self.backend_settings.cmd_trunk_add_vlan,
            "trunk_rem_vlan": self.backend_settings.cmd_trunk_rm_vlan,
            "exit": self.backend_settings.cmd_exit,
            "commit": self.backend_settings.cmd_commit,
            "no_shutdown": self.backend_settings.cmd_no_shutdown,
            "vlan_name": self.backend_settings.cmd_vlan_name,
            "int_description": self.backend_settings.cmd_int_description,
        }

    def _check_ssh_pass_keys(self) -> None:
        """Verify SSH authentication credentials."""
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
            self.switch_config["use_keys"] = True
            self.switch_config["key_file"] = privkey
        elif not self.backend_settings.ssh_password:
            raise AssertionError("No keys or password supplied")

    def _create_configure_commands(self, source_port: str, dest_port: str, vlan: int) -> List[str]:
        """Generate commands to configure VLAN on source and destination ports.

        Args:
            source_port: Source port identifier
            dest_port: Destination port identifier
            vlan: VLAN number to configure

        Returns:
            List of commands to execute
        """
        cmds = self.commands

        # Generate all the commands using the templates
        createvlan = cmds["create_vlan"] % vlan
        intsrc = cmds["interface"] % source_port
        intdst = cmds["interface"] % dest_port
        modetrunk = cmds["mode_trunk"]
        addvlan = cmds["trunk_add_vlan"] % vlan
        cmdexit = cmds["exit"]
        vlanname = cmds["vlan_name"] % vlan
        intsrc_desc = cmds["int_description"] % source_port
        intdst_desc = cmds["int_description"] % dest_port

        # Build command sequence
        commands = [
            createvlan,
            vlanname,
            cmdexit,
            intsrc,
            intsrc_desc,
            modetrunk,
            addvlan,
            cmdexit,
            intdst,
            intdst_desc,
            modetrunk,
            addvlan,
            cmdexit,
        ]
        return commands

    def _create_delete_commands(self, source_port: str, dest_port: str, vlan: int) -> List[str]:
        """Generate commands to remove VLAN from source and destination ports.

        Args:
            source_port: Source port identifier
            dest_port: Destination port identifier
            vlan: VLAN number to remove

        Returns:
            List of commands to execute
        """
        cmds = self.commands

        # Generate all the commands using the templates
        intsrc = cmds["interface"] % source_port
        intdst = cmds["interface"] % dest_port
        remvlan = cmds["trunk_rem_vlan"] % vlan
        cmdexit = cmds["exit"]

        # Build command sequence
        commands = [intsrc, remvlan, cmdexit, intdst, remvlan, cmdexit]
        return commands

    def _send_commands(self, commands: List[str]) -> None:
        """Send commands to the switch via SSH.

        Args:
            commands: List of commands to execute

        Raises:
            NsiException: If there's an error sending commands
        """
        self.log.debug("Sending commands to switch", command_list=commands)

        try:
            self._check_ssh_pass_keys()
            self.log.debug("Establishing SSH connection")

            with ConnectHandler(**self.switch_config) as conn:
                conn.enable()
                self.log.debug("Starting configuration")
                conn.send_config_set(commands)
                self.log.debug("Saving configuration")
                conn.send_command(self.commands["commit"])

        except Exception as exception:
            self.log.warning("Error sending commands to switch", error=str(exception))
            raise NsiException(GenericRmError, "Error sending commands: {0}".format(str(exception))) from exception

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

        if src_vlan != dst_vlan:
            raise NsiException(GenericRmError, "VLANs must match")

        self._send_commands(self._create_configure_commands(src_port_id, dst_port_id, dst_vlan))

        # Generate circuit ID if not provided
        if not circuit_id:
            circuit_id = uuid4().urn

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

        self._send_commands(self._create_delete_commands(src_port_id, dst_port_id, dst_vlan))

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
