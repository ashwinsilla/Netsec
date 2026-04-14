"""
30 AVD evaluation tasks: orthogonal quadrants, insertion paths for sub-tree merge.
Task strings are preserved verbatim from the original avd_harness.py definitions.
"""

from __future__ import annotations

# Repo-relative paths (same layout as Arista single-dc-l3ls example in-tree).
FILE_KEY_TO_CONTEXT = {
    "fabric": "group_vars/FABRIC/fabric_variables.yml",
    "spines": "group_vars/DC1_SPINES/spines.yml",
    "l3leaves": "group_vars/DC1_L3_LEAVES/l3_leaves.yml",
    "netsvcs": "group_vars/NETWORK_SERVICES/network_services.yml",
    "endpoints": "group_vars/CONNECTED_ENDPOINTS/connected_endpoints.yml",
}

QUADRANT_DIRS = {
    1: "quadrant_1_short_flat",
    2: "quadrant_2_short_deep",
    3: "quadrant_3_long_flat",
    4: "quadrant_4_long_deep",
}

# Quadrant layout: 6 + 8 + 8 + 8 = 30 (strategy.md matrix).
# Paths use str keys for dicts and int indices for YAML list positions in the baseline repo.
TASKS: list[dict] = [
    # ── Quadrant 1: short & flat (6) ──────────────────────────────────────
    {
        "id": "P01",
        "quadrant": 1,
        "file_key": "fabric",
        "task_slug": "p01_ntp_second_server",
        "task_text": "Add a second NTP server '1.pool.ntp.org' to the existing ntp_settings.servers list.",
        "insertion_path": ["ntp_settings", "servers"],
    },
    {
        "id": "P02",
        "quadrant": 1,
        "file_key": "fabric",
        "task_slug": "p02_dns_second_server",
        "task_text": "Add a second DNS server with ip_address 8.8.8.8 to dns_settings.servers.",
        "insertion_path": ["dns_settings", "servers"],
    },
    {
        "id": "P03",
        "quadrant": 1,
        "file_key": "fabric",
        "task_slug": "p03_p2p_uplinks_mtu_9214",
        "task_text": "Change p2p_uplinks_mtu from 1500 to 9214.",
        "insertion_path": ["p2p_uplinks_mtu"],
    },
    {
        "id": "P04",
        "quadrant": 1,
        "file_key": "spines",
        "task_slug": "p04_spine_bgp_as_65000",
        "task_text": "Change the BGP ASN for all spines from 65100 to 65000.",
        "insertion_path": ["spine", "defaults", "bgp_as"],
    },
    {
        "id": "P06",
        "quadrant": 1,
        "file_key": "l3leaves",
        "task_slug": "p06_virtual_router_mac",
        "task_text": "Change the virtual_router_mac_address for all l3leaf nodes from 00:1c:73:00:00:99 to 00:1c:73:00:dc:01.",
        "insertion_path": ["l3leaf", "defaults", "virtual_router_mac_address"],
    },
    {
        "id": "P07",
        "quadrant": 1,
        "file_key": "l3leaves",
        "task_slug": "p07_spanning_tree_mode_rstp",
        "task_text": "Change spanning_tree_mode for all l3leaf nodes from mstp to rstp.",
        "insertion_path": ["l3leaf", "defaults", "spanning_tree_mode"],
    },
    # ── Quadrant 2: short & deep (8) ─────────────────────────────────────
    {
        "id": "P08",
        "quadrant": 2,
        "file_key": "fabric",
        "task_slug": "p08_evpn_overlay_peers_password",
        "task_text": "Add a password field with value 'arista123' to the evpn_overlay_peers BGP peer group.",
        "insertion_path": ["bgp_peer_groups", "evpn_overlay_peers", "password"],
    },
    {
        "id": "P09",
        "quadrant": 2,
        "file_key": "l3leaves",
        "task_slug": "p09_spanning_tree_priority_8192",
        "task_text": "Set the spanning_tree_priority for all l3leaf nodes to 8192.",
        "insertion_path": ["l3leaf", "defaults", "spanning_tree_priority"],
    },
    {
        "id": "P10",
        "quadrant": 2,
        "file_key": "fabric",
        "task_slug": "p10_aaa_local_user_netops",
        "task_text": "Add a new local user named 'netops' with privilege 15, role network-operator, and no_password set to true under aaa_settings.local_users.",
        "insertion_path": ["aaa_settings", "local_users"],
    },
    {
        "id": "P11",
        "quadrant": 2,
        "file_key": "l3leaves",
        "task_slug": "p11_dc1_l3_leaf1_filters_tags",
        "task_text": "Add a filters.tags list containing 'DC1_L3_LEAF1' to the DC1_L3_LEAF1 node_group.",
        "insertion_path": ["l3leaf", "node_groups", 0, "filters", "tags"],
    },
    {
        "id": "P12",
        "quadrant": 2,
        "file_key": "l3leaves",
        "task_slug": "p12_dc1_leaf1a_remote_peers",
        "task_text": "Add evpn_gateway.remote_peers as an empty list under dc1-leaf1a's node definition inside DC1_L3_LEAF1.",
        "insertion_path": ["l3leaf", "node_groups", 0, "nodes", 0, "evpn_gateway", "remote_peers"],
    },
    {
        "id": "P13",
        "quadrant": 2,
        "file_key": "netsvcs",
        "task_slug": "p13_vrf11_vtep_diagnostic",
        "task_text": "Add a vtep_diagnostic block to VRF11 with loopback: 12 and loopback_ip_range: 10.255.12.0/27.",
        "insertion_path": ["tenants", 0, "vrfs", 1, "vtep_diagnostic"],
    },
    {
        "id": "P14",
        "quadrant": 2,
        "file_key": "netsvcs",
        "task_slug": "p14_vrf10_static_routes",
        "task_text": "Add a static_routes list to VRF10 containing one route: destination_address_prefix 0.0.0.0/0, gateway 10.10.10.1.",
        "insertion_path": ["tenants", 0, "vrfs", 0, "static_routes"],
    },
    {
        "id": "P15",
        "quadrant": 2,
        "file_key": "netsvcs",
        "task_slug": "p15_svi11_ip_address_virtual",
        "task_text": "Set ip_address_virtual to 10.10.11.254/24 for SVI id 11 inside VRF10, overriding the current value.",
        "insertion_path": ["tenants", 0, "vrfs", 0, "svis", 0, "ip_address_virtual"],
    },
    # ── Quadrant 3: long & flat (8) ──────────────────────────────────────
    {
        "id": "P05",
        "quadrant": 3,
        "file_key": "spines",
        "task_slug": "p05_spine_third_node",
        "task_text": "Add a third spine node named dc1-spine3 with id 3 and mgmt_ip 172.16.1.13/24.",
        "insertion_path": ["spine", "nodes"],
    },
    {
        "id": "P16",
        "quadrant": 3,
        "file_key": "netsvcs",
        "task_slug": "p16_tenant1_vrf20",
        "task_text": "Add a new VRF named VRF20 with vrf_vni 20 to TENANT1. Include one SVI: id 31, name VRF20_VLAN31, enabled true, ip_address_virtual 10.10.31.1/24.",
        "insertion_path": ["tenants", 0, "vrfs"],
    },
    {
        "id": "P17",
        "quadrant": 3,
        "file_key": "l3leaves",
        "task_slug": "p17_uplink_switch_interfaces_defaults",
        "task_text": "Add uplink_switch_interfaces ['Ethernet1', 'Ethernet1'] to the l3leaf defaults section.",
        "insertion_path": ["l3leaf", "defaults", "uplink_switch_interfaces"],
    },
    {
        "id": "P18",
        "quadrant": 3,
        "file_key": "netsvcs",
        "task_slug": "p18_l2vlan_3403",
        "task_text": "Add a new l2vlan to TENANT1 with id 3403 and name L2_VLAN3403.",
        "insertion_path": ["tenants", 0, "l2vlans"],
    },
    {
        "id": "P19",
        "quadrant": 3,
        "file_key": "l3leaves",
        "task_slug": "p19_mlag_port_channel_leaf2",
        "task_text": "Add mlag_port_channel_id: 2000 at the DC1_L3_LEAF2 node_group level (not defaults, not node level).",
        "insertion_path": ["l3leaf", "node_groups", 1, "mlag_port_channel_id"],
    },
    {
        "id": "P20",
        "quadrant": 3,
        "file_key": "netsvcs",
        "task_slug": "p20_svi11_tags",
        "task_text": "Add tags: ['DC1_L3_LEAF1'] to SVI id 11 inside VRF10 so it deploys only on leaf pair 1.",
        "insertion_path": ["tenants", 0, "vrfs", 0, "svis", 0, "tags"],
    },
    {
        "id": "P21",
        "quadrant": 3,
        "file_key": "netsvcs",
        "task_slug": "p21_vrf10_svis_13_14",
        "task_text": "Add two new SVIs to VRF10: id 13 (name VRF10_VLAN13, ip_address_virtual 10.10.13.1/24, enabled true) and id 14 (name VRF10_VLAN14, ip_address_virtual 10.10.14.1/24, enabled true).",
        "insertion_path": ["tenants", 0, "vrfs", 0, "svis"],
    },
    {
        "id": "P22",
        "quadrant": 3,
        "file_key": "endpoints",
        "task_slug": "p22_server_dc1_leaf1_server2",
        "task_text": "Add a new server named dc1-leaf1-server2 with adapters connecting dc1-leaf1a Ethernet6 and dc1-leaf1b Ethernet6, endpoint_ports [PCI1, PCI2], vlans 11-12, mode trunk, spanning_tree_portfast edge, port_channel mode active.",
        "insertion_path": ["servers"],
    },
    # ── Quadrant 4: long & deep (8) ──────────────────────────────────────
    {
        "id": "P23",
        "quadrant": 4,
        "file_key": "netsvcs",
        "task_slug": "p23_tenant2_full",
        "task_text": "Add a new tenant named TENANT2 with mac_vrf_vni_base 20000, VRF VRF20 (vrf_vni 20), and one SVI: id 100, name TENANT2_VLAN100, enabled true, ip_address_virtual 10.20.100.1/24.",
        "insertion_path": ["tenants"],
    },
    {
        "id": "P24",
        "quadrant": 4,
        "file_key": "l3leaves",
        "task_slug": "p24_node_group_dc1_l3_leaf3",
        "task_text": "Add a third node_group DC1_L3_LEAF3 with bgp_as 65103 containing two nodes: dc1-leaf3a (id 5, mgmt_ip 172.16.1.105/24) and dc1-leaf3b (id 6, mgmt_ip 172.16.1.106/24).",
        "insertion_path": ["l3leaf", "node_groups"],
    },
    {
        "id": "P25",
        "quadrant": 4,
        "file_key": "endpoints",
        "task_slug": "p25_dc1_leaf2_server1_ilo",
        "task_text": "Add an iLO adapter to dc1-leaf2-server1: endpoint_ports [iLO], switch_ports [Ethernet6], switches [dc1-leaf2c], vlans 21, mode access, spanning_tree_portfast edge.",
        "insertion_path": ["servers", 1, "adapters"],
    },
    {
        "id": "P26",
        "quadrant": 4,
        "file_key": "netsvcs",
        "task_slug": "p26_svi12_structured_config",
        "task_text": "Add structured_config.description: 'VRF10_VLAN12_CUSTOM' to SVI id 12 in VRF10.",
        "insertion_path": ["tenants", 0, "vrfs", 0, "svis", 1, "structured_config"],
    },
    {
        "id": "P27",
        "quadrant": 4,
        "file_key": "fabric",
        "task_slug": "p27_ntp_multi_edit",
        "task_text": "Add NTP servers 1.pool.ntp.org and 2.pool.ntp.org to ntp_settings.servers, AND set prefer: true on the existing 0.pool.ntp.org server.",
        "insertion_path": ["ntp_settings"],
    },
    {
        "id": "P28",
        "quadrant": 4,
        "file_key": "l3leaves",
        "task_slug": "p28_dc1_leaf1a_structured_config_loopback",
        "task_text": "For node dc1-leaf1a inside DC1_L3_LEAF1, add a structured_config block containing loopback_interfaces: [{name: Loopback0, description: 'ROUTER-ID'}].",
        "insertion_path": ["l3leaf", "node_groups", 0, "nodes", 0, "structured_config"],
    },
    {
        "id": "P29",
        "quadrant": 4,
        "file_key": "netsvcs",
        "task_slug": "p29_svi21_ip_helpers",
        "task_text": "Add ip_helpers to SVI id 21 in VRF11 as a list with one entry: ip_address 10.255.0.1, source_interface Loopback0.",
        "insertion_path": ["tenants", 0, "vrfs", 1, "svis", 0, "ip_helpers"],
    },
    {
        "id": "P30",
        "quadrant": 4,
        "file_key": "l3leaves",
        "task_slug": "p30_evpn_services_l2_only_defaults_and_node",
        "task_text": "Add evpn_services_l2_only: false to l3leaf.defaults, AND add evpn_services_l2_only: true as a node-level override on dc1-leaf2a only inside DC1_L3_LEAF2.",
        "insertion_path": ["l3leaf"],
    },
]


def task_context_file(task: dict) -> str:
    return FILE_KEY_TO_CONTEXT[task["file_key"]]


def enrich_task(task: dict) -> dict:
    out = {**task, "context_file": task_context_file(task)}
    out["quadrant_dir"] = QUADRANT_DIRS[task["quadrant"]]
    return out


def all_tasks() -> list[dict]:
    return [enrich_task(t) for t in TASKS]


def task_by_id(task_id: str) -> dict | None:
    tid = task_id.upper() if task_id.lower().startswith("p") else task_id
    for t in TASKS:
        if t["id"].upper() == tid.upper():
            return enrich_task(t)
    return None


def _assert_quadrant_counts() -> None:
    from collections import Counter

    c = Counter(t["quadrant"] for t in TASKS)
    assert len(TASKS) == 30
    assert c[1] == 6 and c[2] == 8 and c[3] == 8 and c[4] == 8


_assert_quadrant_counts()
