from typing import Any


def test_conftest_backend_vlans_and_ports(backend_testing_vlans_ports: Any) -> Any:
    """Test backend to return vlans and ports."""
    assert backend_testing_vlans_ports["src_port_id"] == "Ethernet 1"
    assert backend_testing_vlans_ports["dst_port_id"] == "Ethernet 2"
    assert backend_testing_vlans_ports["dst_vlan"] == 1799


def test_backend_setup_vlan(backend_testing_vlans_ports: Any) -> Any:
    """Test that backend activation returns a circuit ID string."""
    from supa.nrm.backend import backend as backend

    assert isinstance(backend.activate(**backend_testing_vlans_ports), str)


def test_backend_teardown_vlan(backend_testing_vlans_ports: Any) -> Any:
    """Test that backend deactivation returns None."""
    from supa.nrm.backend import backend

    assert backend.deactivate(**backend_testing_vlans_ports) is None


def test_backend_setup_and_teardown_vlan(backend_testing_vlans_ports: Any) -> Any:
    """Test full setup and teardown cycle with backend."""
    from supa.nrm.backend import backend

    assert isinstance(backend.activate(**backend_testing_vlans_ports), str)
    assert backend.deactivate(**backend_testing_vlans_ports) is None


def test_backend_setup_and_teardown_vlan_bulk(backend_testing_vlans_ports: Any, src_port_id, dst_port_id, dst_vlan):
    """Test bulk setup and teardown with parameters from command line."""
    assert backend_testing_vlans_ports["src_port_id"] == src_port_id
    assert backend_testing_vlans_ports["dst_port_id"] == dst_port_id
    assert backend_testing_vlans_ports["dst_vlan"] == dst_vlan
    # print("src_port_id:", src_port_id)
    # print("dst_port_id:", dst_port_id)
    # print("dst_vlan:", dst_vlan)
    from supa.nrm.backend import backend

    assert isinstance(backend.activate(**backend_testing_vlans_ports), str)
    # assert backend.deactivate(**backend_testing_vlans_ports) is None
