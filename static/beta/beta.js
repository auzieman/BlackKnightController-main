(function () {
    const cyEl = document.getElementById("beta-cy");
    if (!cyEl || !window.cytoscape) return;

    let elements = [];
    try {
        elements = JSON.parse(cyEl.dataset.elements || "[]");
    } catch (err) {
        cyEl.textContent = `Graph payload failed to parse: ${err}`;
        return;
    }

    seedMissingPositions(elements);
    addIconLabels(elements);

    const cy = cytoscape({
        container: cyEl,
        elements,
        wheelSensitivity: 0.22,
        style: [
            {
                selector: "node",
                style: {
                    "background-color": "#183047",
                    "background-opacity": 0.94,
                    "border-color": "#7892ad",
                    "border-width": 2,
                    color: "#eef6ff",
                    label: "data(iconLabel)",
                    "font-size": 12,
                    "font-weight": 800,
                    "text-valign": "center",
                    "text-halign": "center",
                    "text-outline-color": "#020617",
                    "text-outline-width": 4,
                    width: 68,
                    height: 56,
                    shape: "round-rectangle",
                },
            },
            {
                selector: "$node",
                style: {
                    "background-color": "#0b1826",
                    "background-opacity": 0.07,
                    "border-color": "#49627d",
                    "border-opacity": 0.24,
                    "border-style": "dashed",
                    "border-width": 1,
                    "compound-sizing-wrt-labels": "exclude",
                    label: "",
                    padding: "22px",
                    shape: "round-rectangle",
                    "text-opacity": 0,
                    "z-index": 0,
                },
            },
            {
                selector: "$node.focus-neighbor, $node.focus-root",
                style: {
                    "background-opacity": 0.1,
                    "border-opacity": 0.48,
                    "border-color": "#a78bfa",
                    label: "data(label)",
                    "text-opacity": 0.72,
                    color: "#b5c7da",
                    "font-size": 11,
                },
            },
            {
                selector: "node[type = 'pipeline_group']",
                style: {
                    "background-color": "#26160c",
                    "background-opacity": 0.06,
                    "border-color": "#fb923c",
                    "border-opacity": 0.32,
                },
            },
            {
                selector: "node[state = 'failed'], node[status = 'failed']",
                style: { "border-color": "#fb7185", "background-color": "#24101a" },
            },
            {
                selector: "node[state = 'offline'], node[status = 'offline'], node[state = 'stale'], node[status = 'stale']",
                style: {
                    "background-color": "#101827",
                    "background-opacity": 0.62,
                    "border-color": "#64748b",
                    "border-style": "dashed",
                    color: "#cbd5e1",
                    opacity: 0.72,
                },
            },
            {
                selector: "node[state = 'running'], node[status = 'running']",
                style: { "border-color": "#4d7c0f", "background-opacity": 0.78 },
            },
            {
                selector: "node[kind *= 'pipeline']",
                style: { "border-color": "#fb923c", shape: "round-rectangle" },
            },
            {
                selector: "node[type = 'pipeline'], node[type = 'stage']",
                style: { "border-color": "#fb923c", shape: "round-rectangle" },
            },

            {
                selector: "node[type = 'firewall']",
                style: { "background-color": "#573044", "border-color": "#dc7686", color: "#fff0f3", shape: "hexagon", width: 74, height: 64, "font-size": 12 },
            },
            {
                selector: "node[type = 'isp']",
                style: { "background-color": "#1d2634", "border-color": "#74839a", color: "#d7e1ef", shape: "ellipse", width: 78, height: 58, "font-size": 12, "border-style": "dashed" },
            },
            {
                selector: "node[type = 'switch']",
                style: { "background-color": "#1f5659", "border-color": "#6bbfb8", color: "#edfffb", shape: "round-rectangle", width: 84, height: 52, "font-size": 12 },
            },
            {
                selector: "node[type = 'bmc']",
                style: { "background-color": "#463b76", "border-color": "#a89ee5", color: "#fbf8ff", shape: "rectangle", width: 68, height: 68, "font-size": 12 },
            },
            {
                selector: "node[type = 'host']",
                style: { "background-color": "#254d72", "border-color": "#8aaec8", color: "#f1f8ff", shape: "round-rectangle", width: 82, height: 58, "font-size": 12 },
            },
            {
                selector: "node[type = 'cluster']",
                style: { "background-color": "#62451d", "border-color": "#d5a14b", color: "#fff6e8", shape: "ellipse", width: 96, height: 70, "font-size": 12 },
            },
            {
                selector: "node[type = 'vm']",
                style: { "background-color": "#24576b", "border-color": "#7bcce1", color: "#efffff", shape: "diamond", width: 72, height: 72, "font-size": 11 },
            },
            {
                selector: "node[type = 'container']",
                style: { "background-color": "#2a5a3d", "border-color": "#8ed89f", color: "#f3fff6", shape: "round-rectangle", width: 82, height: 54, "font-size": 11 },
            },
            {
                selector: "node[type = 'stack']",
                style: { "background-color": "#4d3a77", "border-color": "#bfa4e3", color: "#fff6ff", shape: "round-rectangle", width: 86, height: 56, "font-size": 11 },
            },
            {
                selector: "node[type = 'evidence']",
                style: { "background-color": "#30343f", "border-color": "#d6c58a", color: "#fff8dc", shape: "round-rectangle", width: 98, height: 58, "font-size": 11, "border-style": "double" },
            },
            {
                selector: "node[type = 'interface']",
                style: { "background-color": "#1a3f4b", "border-color": "#74c7d5", color: "#ecfeff", shape: "tag", width: 96, height: 50, "font-size": 10 },
            },
            {
                selector: "node[expandable = 'true']",
                style: {
                    "border-style": "double",
                    "border-width": 3,
                    "shadow-blur": 12,
                    "shadow-color": "#2dd4bf",
                    "shadow-opacity": 0.16,
                    "shadow-offset-x": 0,
                    "shadow-offset-y": 0,
                },
            },
            {
                selector: "edge",
                style: {
                    width: 1.55,
                    "line-color": "#526780",
                    "target-arrow-color": "#526780",
                    "target-arrow-shape": "triangle",
                    "curve-style": "bezier",
                    opacity: 0.48,
                },
            },
            {
                selector: ":selected",
                style: {
                    "border-color": "#fb923c",
                    "line-color": "#fb923c",
                    "target-arrow-color": "#fb923c",
                },
            },
            {
                selector: ".dimmed",
                style: { opacity: 0.38 },
            },
            {
                selector: ".hidden-stale",
                style: { display: "none" },
            },
            {
                selector: ".hidden-pipeline",
                style: { display: "none" },
            },
            {
                selector: ".focus-neighbor",
                style: {
                    opacity: 1,
                    "border-width": 3,
                    "border-color": "#8bd3dd",
                    "line-color": "#8bd3dd",
                    "target-arrow-color": "#8bd3dd",
                    width: 84,
                    height: 66,
                    "font-size": 12,
                },
            },
            {
                selector: ".focus-root",
                style: {
                    opacity: 1,
                    width: 108,
                    height: 84,
                    "border-width": 5,
                    "border-color": "#f6b75d",
                    "font-size": 14,
                    "shadow-blur": 26,
                    "shadow-color": "#f6b75d",
                    "shadow-opacity": 0.32,
                },
            },
            {
                selector: "edge.focus-neighbor",
                style: {
                    width: 3,
                    opacity: 0.95,
                    "line-color": "#8bd3dd",
                    "target-arrow-color": "#8bd3dd",
                },
            },
            {
                selector: "edge.focus-root",
                style: {
                    width: 4,
                    opacity: 1,
                    "line-color": "#f6b75d",
                    "target-arrow-color": "#f6b75d",
                },
            },
            {
                selector: "node.search-hit",
                style: {
                    "border-color": "#e5c07b",
                    "border-width": 5,
                    "background-opacity": 1,
                    "font-size": 13,
                    "shadow-blur": 18,
                    "shadow-color": "#e5c07b",
                    "shadow-opacity": 0.24,
                },
            },
            {
                selector: "edge.search-path",
                style: {
                    width: 3.2,
                    opacity: 0.96,
                    "line-color": "#88c0d0",
                    "target-arrow-color": "#88c0d0",
                },
            },
        ],
        layout: {
            name: "preset",
            fit: true,
            padding: 55,
        },
    });

    function seedMissingPositions(payload) {
        const nodes = payload.nodes || [];
        let index = 0;
        nodes.forEach((node) => {
            if (node.position) return;
            const angle = (Math.PI * 2 * index) / Math.max(1, nodes.length);
            const ring = 230 + (index % 3) * 42;
            node.position = { x: 430 + Math.cos(angle) * ring, y: 230 + Math.sin(angle) * ring };
            index += 1;
        });
    }

    function addIconLabels(payload) {
        const icons = {
            firewall: "🛡",
            isp: "⌁",
            switch: "▦",
            bmc: "▣",
            host: "▤",
            cluster: "☁",
            vm: "◇",
            container: "▱",
            stack: "▥",
            evidence: "⌕",
            interface: "∿",
            pipeline: "▶",
            stage: "→",
        };
        (payload.nodes || []).forEach((node) => {
            const data = node.data || {};
            const type = String(data.type || "").toLowerCase();
            const icon = icons[type] || "●";
            data.iconLabel = `${icon}
${data.label || data.id || ""}`;
        });
    }

    const expansionPacks = {
        "platform:hypervisors": {
            summary: "Hypervisor tier expands into the current lab hosts.",
            nodes: [
                { id: "host:pve1", label: "pve1\nProxmox .9", type: "host", kind: "hypervisor", status: "running", ip: "192.168.1.9 / 10.20.0.9", platform: "Proxmox VE", role: "edge / legacy workloads", breadcrumb: "Hypervisors › pve1 › ns1/swarm/IPFire/kube", inventory: "live service reachable; API credential needs refresh", last_seen: "reachable now on 8006" },
                { id: "host:server1", label: "server1\nOpenStack", type: "host", kind: "hypervisor", status: "running", ip: "10.20.0.240", platform: "OpenStack lab", role: "future BKC home", breadcrumb: "Hypervisors › server1 › OpenStack", last_seen: "reachable now on 22/443" },
                { id: "host:server2", label: "server2\nESXi", type: "host", kind: "hypervisor", status: "running", ip: "10.20.0.114", platform: "VMware ESXi 8", role: "API / SSH lab", breadcrumb: "Hypervisors › server2 › ESXi swarm lab", last_seen: "reachable now on 22/443" },
            ],
            edges: [
                ["platform:hypervisors", "host:pve1", "contains"],
                ["platform:hypervisors", "host:server1", "contains"],
                ["platform:hypervisors", "host:server2", "contains"],
                ["fabric:n2024", "host:pve1", "switches"],
                ["fabric:n2024", "host:server1", "switches"],
                ["fabric:n2024", "host:server2", "switches"],
            ],
            layout: { mode: "fan", direction: "right", arc: 96, distance: 280 },
        },
        "host:pve1": {
            summary: "Edge Proxmox expands into the living lab VMs. This is intentionally separate from the stale Server2 Proxmox snapshot.",
            nodes: [
                { id: "vm:ns1.lab.auzietek.com", label: "ns1\nDNS/NFS/PXE", type: "vm", kind: "core-vm", status: "running", ip: "10.20.0.10", platform: "Proxmox .9", role: "DHCP / DNS / NFS / PXE", parent_host: "pve1", breadcrumb: "pve1 › ns1 › DNS/DHCP/PXE/NFS", last_seen: "reachable now on 22" },
                { id: "vm:swarm1.lab.auzietek.com", label: "swarm1\nBKC", type: "vm", kind: "swarm-manager", status: "running", ip: "10.20.0.15", platform: "Proxmox .9", role: "current Docker Swarm / BKC", parent_host: "pve1", breadcrumb: "pve1 › swarm1 › blackknight/lab-edge/monitoring/portainer/registry", last_seen: "docker stack ls responding" },
                { id: "vm:swarm2.lab.auzietek.com", label: "swarm2", type: "vm", kind: "swarm-worker", status: "stale", platform: "Proxmox .9", role: "legacy Docker Swarm worker", parent_host: "pve1", breadcrumb: "pve1 › swarm2", last_seen: "known from inventory; refresh pending" },
                { id: "vm:swarm3.lab.auzietek.com", label: "swarm3", type: "vm", kind: "swarm-worker", status: "stale", platform: "Proxmox .9", role: "legacy Docker Swarm worker", parent_host: "pve1", breadcrumb: "pve1 › swarm3", last_seen: "known from inventory; refresh pending" },
                { id: "vm:pve-ipfire", label: "IPFire\nedge", type: "vm", kind: "firewall-vm", status: "running", ip: "10.20.0.254 / 192.168.1.82", platform: "Proxmox .9", role: "candidate NAT / firewall / VPN", parent_host: "pve1", breadcrumb: "pve1 › IPFire › red/green networks", last_seen: "reachable now on 22" },
                { id: "vm:pve-kube", label: "kube\noffline", type: "vm", kind: "kube-lab", status: "offline", platform: "Proxmox .9", role: "parked Kubernetes lab", parent_host: "pve1", breadcrumb: "pve1 › kube offline", last_seen: "intentionally parked" },
                { id: "evidence:pve1-mac-adjacency", label: "MAC\nEvidence", type: "evidence", kind: "mac-adjacency", status: "running", role: "bridge / ARP / switch proof", breadcrumb: "pve1 › evidence › MAC tables", last_seen: "fresh SSH probe" },
            ],
            edges: [
                ["host:pve1", "vm:ns1.lab.auzietek.com", "runs"],
                ["host:pve1", "vm:swarm1.lab.auzietek.com", "runs"],
                ["host:pve1", "vm:swarm2.lab.auzietek.com", "runs"],
                ["host:pve1", "vm:swarm3.lab.auzietek.com", "runs"],
                ["host:pve1", "vm:pve-ipfire", "runs"],
                ["host:pve1", "vm:pve-kube", "runs"],
                ["host:pve1", "evidence:pve1-mac-adjacency", "proven_by"],
                ["vm:ns1.lab.auzietek.com", "core:ns1", "represents"],
                ["vm:pve-ipfire", "edge:ipfire", "represents"],
            ],
            layout: { mode: "fan", direction: "right", arc: 122, distance: 320 },
        },
        "evidence:pve1-mac-adjacency": {
            summary: "MAC evidence links pve1 to its bridges, VM NICs, ns1 ARP observations, and switch-facing uplinks.",
            nodes: [
                { id: "iface:pve1-vmbr0", label: "vmbr0\nLAN", type: "interface", kind: "linux-bridge", status: "running", mac: "fc:4d:d4:3d:fa:c9", ip: "192.168.1.9", seen_on: "pve1 enp8s0 / ns1 ens18", breadcrumb: "pve1 › vmbr0 › LAN side" },
                { id: "iface:pve1-vmbr20", label: "vmbr20\n10.20", type: "interface", kind: "linux-bridge", status: "running", mac: "fc:4d:d4:3d:fa:c8", ip: "10.20.0.9", seen_on: "pve1 eno1 / ns1 ens19", breadcrumb: "pve1 › vmbr20 › management/private side" },
                { id: "iface:swarm1-net0", label: "swarm1\n8a:43", type: "interface", kind: "vm-nic", status: "running", mac: "8a:43:82:22:74:16", vmid: "127", bridge: "vmbr0", seen_on: "pve1 bridge fdb" },
                { id: "iface:swarm2-net0", label: "swarm2\n72:21", type: "interface", kind: "vm-nic", status: "running", mac: "72:21:b3:42:51:aa", vmid: "126", bridge: "vmbr0", seen_on: "pve1 bridge fdb" },
                { id: "iface:swarm3-net0", label: "swarm3\nd6:13", type: "interface", kind: "vm-nic", status: "running", mac: "d6:13:71:52:4b:d5", vmid: "128", bridge: "vmbr0", seen_on: "pve1 bridge fdb" },
                { id: "iface:ipfire-red", label: "IPFire\nRED", type: "interface", kind: "vm-nic", status: "running", mac: "4a:06:95:a0:9e:80", vmid: "140", bridge: "vmbr0", ip: "192.168.1.82", seen_on: "ns1 ARP + pve1 qm config" },
                { id: "iface:ipfire-green", label: "IPFire\nGREEN", type: "interface", kind: "vm-nic", status: "running", mac: "66:f9:9b:76:d1:0c", vmid: "140", bridge: "vmbr20", ip: "10.20.0.254", seen_on: "ns1 ARP + pve1 qm config" },
                { id: "iface:ns1-lan", label: "ns1\nLAN", type: "interface", kind: "vm-nic", status: "running", mac: "0e:80:31:c3:a0:53", vmid: "129", bridge: "vmbr0", seen_on: "pve1 qm config" },
                { id: "iface:ns1-mgmt", label: "ns1\n10.20", type: "interface", kind: "vm-nic", status: "running", mac: "f2:fe:79:04:c4:6c", vmid: "129", bridge: "vmbr20", seen_on: "pve1 qm config" },
            ],
            edges: [
                ["evidence:pve1-mac-adjacency", "iface:pve1-vmbr0", "observed"],
                ["evidence:pve1-mac-adjacency", "iface:pve1-vmbr20", "observed"],
                ["iface:pve1-vmbr0", "fabric:n2024", "switch_port_seen"],
                ["iface:pve1-vmbr20", "fabric:n2024", "switch_port_seen"],
                ["vm:swarm1.lab.auzietek.com", "iface:swarm1-net0", "has_mac"],
                ["vm:swarm2.lab.auzietek.com", "iface:swarm2-net0", "has_mac"],
                ["vm:swarm3.lab.auzietek.com", "iface:swarm3-net0", "has_mac"],
                ["vm:pve-ipfire", "iface:ipfire-red", "has_mac"],
                ["vm:pve-ipfire", "iface:ipfire-green", "has_mac"],
                ["vm:ns1.lab.auzietek.com", "iface:ns1-lan", "has_mac"],
                ["vm:ns1.lab.auzietek.com", "iface:ns1-mgmt", "has_mac"],
                ["iface:swarm1-net0", "iface:pve1-vmbr0", "on_bridge"],
                ["iface:swarm2-net0", "iface:pve1-vmbr0", "on_bridge"],
                ["iface:swarm3-net0", "iface:pve1-vmbr0", "on_bridge"],
                ["iface:ipfire-red", "iface:pve1-vmbr0", "on_bridge"],
                ["iface:ns1-lan", "iface:pve1-vmbr0", "on_bridge"],
                ["iface:ipfire-green", "iface:pve1-vmbr20", "on_bridge"],
                ["iface:ns1-mgmt", "iface:pve1-vmbr20", "on_bridge"],
            ],
            layout: { mode: "fan", direction: "right", arc: 150, distance: 300 },
        },
        "host:server2": {
            summary: "ESXi expands into the staged Docker Swarm VM set.",
            nodes: [
                { id: "vm:esxi-swarm-mgr-01", label: "manager1", type: "vm", kind: "swarm-manager", status: "running", cpu: "4 vCPU", ram: "4 GB", disk: "100 GB", platform: "ESXi" },
                { id: "vm:esxi-swarm-mgr-02", label: "manager2", type: "vm", kind: "swarm-manager", status: "running", cpu: "4 vCPU", ram: "4 GB", disk: "100 GB", platform: "ESXi" },
                { id: "vm:esxi-swarm-worker-01", label: "worker1", type: "vm", kind: "swarm-worker", status: "running", cpu: "8 vCPU", ram: "16 GB", disk: "500 GB", platform: "ESXi" },
                { id: "vm:esxi-swarm-worker-02", label: "worker2", type: "vm", kind: "swarm-worker", status: "running", cpu: "8 vCPU", ram: "16 GB", disk: "500 GB", platform: "ESXi" },
                { id: "vm:esxi-swarm-worker-03", label: "worker3", type: "vm", kind: "swarm-worker", status: "running", cpu: "8 vCPU", ram: "16 GB", disk: "500 GB", platform: "ESXi" },
            ],
            edges: [
                ["host:server2", "vm:esxi-swarm-mgr-01", "runs"],
                ["host:server2", "vm:esxi-swarm-mgr-02", "runs"],
                ["host:server2", "vm:esxi-swarm-worker-01", "runs"],
                ["host:server2", "vm:esxi-swarm-worker-02", "runs"],
                ["host:server2", "vm:esxi-swarm-worker-03", "runs"],
                ["vm:esxi-swarm-mgr-01", "vm:esxi-swarm-mgr-02", "raft-peer"],
                ["vm:esxi-swarm-mgr-01", "vm:esxi-swarm-worker-01", "swarm-controls"],
                ["vm:esxi-swarm-mgr-01", "vm:esxi-swarm-worker-02", "swarm-controls"],
                ["vm:esxi-swarm-mgr-01", "vm:esxi-swarm-worker-03", "swarm-controls"],
            ],
            layout: { mode: "fan", direction: "right", arc: 118, distance: 310 },
        },
        "host:r630-proxmox-01": {
            summary: "Stale Server2 hypervisor snapshot. Kept as evidence, but no longer treated as the edge pve1 parent.",
            nodes: [
                { id: "vm:stale-ns1-trixie-base", label: "ns1\nbase", type: "vm", kind: "stale-template", status: "stale", platform: "legacy Server2 snapshot", breadcrumb: "r630-proxmox-01 › ns1-trixie-base", last_seen: "snapshot only" },
                { id: "vm:stale-swarm1-trixie-base", label: "swarm1\nbase", type: "vm", kind: "stale-template", status: "stale", platform: "legacy Server2 snapshot", breadcrumb: "r630-proxmox-01 › swarm1-trixie-base", last_seen: "snapshot only" },
            ],
            edges: [
                ["host:r630-proxmox-01", "vm:stale-ns1-trixie-base", "snapshot_had"],
                ["host:r630-proxmox-01", "vm:stale-swarm1-trixie-base", "snapshot_had"],
            ],
            layout: { start: -54, step: 108, distance: 230 },
        },
        "vm:esxi-swarm-mgr-02": {
            summary: "Second manager mirrors the control-plane and quorum services.",
            nodes: [
                { id: "stack:raft-quorum-esxi", label: "Raft\nQuorum", type: "stack", kind: "docker-control-plane", status: "running", services: "manager quorum" },
                { id: "stack:portainer-backup-esxi", label: "Portainer\nBackup", type: "stack", kind: "docker-stack", status: "candidate", services: "agent / failover" },
            ],
            edges: [
                ["vm:esxi-swarm-mgr-02", "stack:raft-quorum-esxi", "hosts"],
                ["vm:esxi-swarm-mgr-02", "stack:portainer-backup-esxi", "hosts"],
            ],
            layout: { start: -52, step: 104, distance: 220 },
        },
        "vm:esxi-swarm-worker-01": {
            summary: "Worker node expands into application workload lanes.",
            nodes: [
                { id: "container:worker1-web", label: "web\nlane", type: "container", kind: "docker-service", status: "candidate", services: "frontend/API tasks" },
                { id: "container:worker1-tools", label: "tool\nlane", type: "container", kind: "docker-service", status: "candidate", services: "automation helpers" },
            ],
            edges: [
                ["vm:esxi-swarm-worker-01", "container:worker1-web", "runs"],
                ["vm:esxi-swarm-worker-01", "container:worker1-tools", "runs"],
            ],
            layout: { start: -52, step: 104, distance: 220 },
        },
        "vm:esxi-swarm-worker-02": {
            summary: "Worker node expands into observability/storage workload lanes.",
            nodes: [
                { id: "container:worker2-monitoring", label: "metrics", type: "container", kind: "docker-service", status: "candidate", services: "exporters / collectors" },
                { id: "container:worker2-registry", label: "registry", type: "container", kind: "docker-service", status: "candidate", services: "image cache" },
            ],
            edges: [
                ["vm:esxi-swarm-worker-02", "container:worker2-monitoring", "runs"],
                ["vm:esxi-swarm-worker-02", "container:worker2-registry", "runs"],
            ],
            layout: { start: -52, step: 104, distance: 220 },
        },
        "vm:esxi-swarm-worker-03": {
            summary: "Worker node expands into AI and demo workload lanes.",
            nodes: [
                { id: "container:worker3-openwebui", label: "OpenWebUI", type: "container", kind: "docker-service", status: "candidate", services: "chat UI" },
                { id: "container:worker3-ollama", label: "Ollama", type: "container", kind: "docker-service", status: "candidate", services: "model runtime" },
            ],
            edges: [
                ["vm:esxi-swarm-worker-03", "container:worker3-openwebui", "runs"],
                ["vm:esxi-swarm-worker-03", "container:worker3-ollama", "runs"],
                ["container:worker3-openwebui", "container:worker3-ollama", "calls"],
            ],
            layout: { start: -52, step: 104, distance: 220 },
        },
        "host:server1": {
            summary: "OpenStack expands into the first lab tenant instances.",
            nodes: [
                { id: "vm:openstack-manager-01", label: "os-manager1", type: "vm", kind: "swarm-manager", status: "planned", platform: "OpenStack", role: "future BKC/swarm manager" },
                { id: "vm:openstack-worker-01", label: "os-worker1", type: "vm", kind: "swarm-worker", status: "planned", platform: "OpenStack" },
                { id: "vm:openstack-worker-02", label: "os-worker2", type: "vm", kind: "swarm-worker", status: "planned", platform: "OpenStack" },
            ],
            edges: [
                ["host:server1", "vm:openstack-manager-01", "runs"],
                ["host:server1", "vm:openstack-worker-01", "runs"],
                ["host:server1", "vm:openstack-worker-02", "runs"],
                ["platform:openstack", "host:server1", "backed_by"],
            ],
            layout: { start: -82, step: 82, distance: 260 },
        },
        "vm:esxi-swarm-mgr-01": {
            summary: "Swarm manager expands into visible service stacks.",
            nodes: [
                { id: "stack:portainer-agent-esxi", label: "Portainer\nAgent", type: "stack", kind: "docker-stack", status: "running", endpoint: "Portainer" },
                { id: "stack:monitoring-esxi", label: "Monitoring", type: "stack", kind: "docker-stack", status: "planned", services: "grafana/prometheus/exporters" },
                { id: "stack:registry-esxi", label: "Registry", type: "stack", kind: "docker-stack", status: "planned", services: "registry cache" },
                { id: "stack:openwebui-esxi", label: "OpenWebUI", type: "stack", kind: "docker-stack", status: "candidate", services: "ollama/openwebui" },
            ],
            edges: [
                ["vm:esxi-swarm-mgr-01", "stack:portainer-agent-esxi", "orchestrates"],
                ["vm:esxi-swarm-mgr-01", "stack:monitoring-esxi", "orchestrates"],
                ["vm:esxi-swarm-mgr-01", "stack:registry-esxi", "orchestrates"],
                ["vm:esxi-swarm-mgr-01", "stack:openwebui-esxi", "orchestrates"],
            ],
            layout: { start: -96, step: 64, distance: 230 },
        },
        "vm:openstack-manager-01": {
            summary: "OpenStack manager expands into the future BKC home services.",
            nodes: [
                { id: "stack:blackknight-openstack", label: "BKC", type: "stack", kind: "docker-stack", status: "planned", services: "api/ui/worker" },
                { id: "stack:lab-edge-openstack", label: "Edge\nProxy", type: "stack", kind: "docker-stack", status: "planned", services: "nginx routes" },
                { id: "stack:monitoring-openstack", label: "Monitoring", type: "stack", kind: "docker-stack", status: "planned", services: "grafana/prometheus" },
            ],
            edges: [
                ["vm:openstack-manager-01", "stack:blackknight-openstack", "orchestrates"],
                ["vm:openstack-manager-01", "stack:lab-edge-openstack", "orchestrates"],
                ["vm:openstack-manager-01", "stack:monitoring-openstack", "orchestrates"],
            ],
            layout: { start: -76, step: 76, distance: 225 },
        },
    };

    const expandedPacks = new Set();
    const expansionAliases = {
        "host:proxmox1.lab.auzietek.com:": "host:pve1",
        "vm:swarm1.lab.auzietek.com:": "vm:swarm1.lab.auzietek.com",
        "vm:ns1.lab.auzietek.com:": "vm:ns1.lab.auzietek.com",
    };
    markExpandableNodes();

    const title = document.getElementById("beta-selected-title");
    const kind = document.getElementById("beta-selected-kind");
    const facts = document.getElementById("beta-selected-facts");
    const openCurrent = document.getElementById("beta-open-current");
    const relationshipCard = document.getElementById("beta-relationship-card");
    const codeView = document.getElementById("beta-code-view");
    const actionTitle = document.getElementById("beta-action-title");
    const actionCopy = document.getElementById("beta-action-copy");
    const actionPayload = document.getElementById("beta-action-payload");
    const nodePopover = document.getElementById("beta-node-popover");
    let selectedContext = { type: "none", id: "", label: "", data: {} };
    let selectedNodeId = "";
    let showStale = false;
    const grafanaBaseUrl = "http://swarm1.lab.auzietek.com:8085";
    const grafanaActivityDashboard = `${grafanaBaseUrl}/d/bfppoy5unfpxcf/blackknightcontroller-activity`;

    function renderSelection(node) {
        const data = node.data();
        selectedNodeId = node.id();
        expandGraphPack(selectedNodeId, { auto: true });
        selectedContext = {
            type: "resource",
            id: data.id || "",
            label: data.label || data.name || data.id || "Resource",
            data,
        };
        title.textContent = data.label || data.name || data.id || "Selected resource";
        kind.textContent = [data.kind, data.status || data.state].filter(Boolean).join(" · ") || "resource";
        const hidden = new Set(["id", "label", "name", "parent"]);
        const rows = Object.entries(data)
            .filter(([key, value]) => !hidden.has(key) && value !== null && value !== undefined && String(value) !== "")
            .slice(0, 10);
        facts.innerHTML = rows.map(([key, value]) => (
            `<div class="fact-item"><span>${escapeHtml(key)}</span>${escapeHtml(String(value))}</div>`
        )).join("");
        openCurrent.href = `/resources?resource=${encodeURIComponent(data.id || "")}`;
        focusNeighborhood(node);
        if (nodePopover) nodePopover.hidden = true;
        renderRelationshipCard(node);
        renderCode(data);
        renderActionDraft("inspect");
    }

    function neighborhoodByDepth(node, depth) {
        let collection = node;
        let frontier = node;
        for (let i = 0; i < depth; i += 1) {
            frontier = frontier.connectedEdges().connectedNodes();
            collection = collection.union(frontier).union(frontier.connectedEdges());
        }
        return collection;
    }

    function applyScope(depth) {
        const node = selectedNodeId ? cy.getElementById(selectedNodeId) : cy.nodes(":selected").first();
        if (!node || node.empty()) return;
        cy.elements().removeClass("dimmed focus-root focus-neighbor");
        if (depth === "all") {
            cy.fit(undefined, 45);
            return;
        }
        cy.elements().addClass("dimmed");
        const scoped = neighborhoodByDepth(node, depth);
        scoped.removeClass("dimmed").addClass("focus-neighbor");
        node.removeClass("focus-neighbor").addClass("focus-root");
        cy.animate({ fit: { eles: scoped, padding: 70 } }, { duration: 240 });
    }

    function focusNeighborhood(node) {
        applyScope(1);
    }

    function showNodePopover(node) {
        if (!nodePopover) return;
        const data = node.data();
        const pos = node.renderedPosition();
        const width = nodePopover.offsetWidth || 220;
        const x = Math.max(12, Math.min(cyEl.clientWidth - width - 12, pos.x + 22));
        const y = Math.max(56, Math.min(cyEl.clientHeight - 120, pos.y + 18));
        nodePopover.hidden = false;
        nodePopover.style.left = `${x}px`;
        nodePopover.style.top = `${y}px`;
        nodePopover.querySelector(".node-popover-title").textContent = data.label || data.name || data.id || "Resource";
        nodePopover.querySelector(".node-popover-kind").textContent = [data.kind, data.status || data.state].filter(Boolean).join(" · ") || "resource";
    }

    function monitoringIdentity(data) {
        const haystack = [
            data.id,
            data.label,
            data.name,
            data.kind,
            data.ip,
            data.platform,
            data.role,
            data.parent_host,
        ].filter(Boolean).join(" ").toLowerCase();
        if (haystack.includes("pve1") || haystack.includes("192.168.1.9") || haystack.includes("proxmox .9")) {
            return { host: "pve1", instance: "192.168.1.9", role: "proxmox" };
        }
        if (haystack.includes("server1") || haystack.includes("openstack") || haystack.includes("10.20.0.240")) {
            return { host: "server1", instance: "10.20.0.240", role: "openstack" };
        }
        if (haystack.includes("server2") || haystack.includes("esxi") || haystack.includes("10.20.0.114")) {
            return { host: "server2", instance: "10.20.0.114", role: "esxi" };
        }
        if (haystack.includes("swarm1") || haystack.includes("10.20.0.15")) {
            return { host: "swarm1", instance: "10.20.0.15", role: "docker-swarm" };
        }
        if (haystack.includes("ns1") || haystack.includes("10.20.0.10")) {
            return { host: "ns1", instance: "10.20.0.10", role: "dns-dhcp-nfs" };
        }
        if (haystack.includes("ipfire") || haystack.includes("10.20.0.254")) {
            return { host: "ipfire", instance: "10.20.0.254", role: "firewall" };
        }
        return {
            host: String(data.label || data.name || data.id || "resource").split(/\s|\n/)[0],
            instance: String(data.ip || "").split(/[\s/]+/)[0],
            role: String(data.kind || data.type || "resource"),
        };
    }

    function grafanaUrlForNode(data) {
        if (data.grafana_url || data.dashboard_url || data.metrics_url) {
            return data.grafana_url || data.dashboard_url || data.metrics_url;
        }
        const identity = monitoringIdentity(data);
        const params = new URLSearchParams({
            orgId: "1",
            refresh: "30s",
            "var-host": identity.host || "",
            "var-node": identity.host || "",
            "var-instance": identity.instance || "",
            "var-role": identity.role || "",
        });
        return `${grafanaActivityDashboard}?${params.toString()}`;
    }

    function openMetricsForSelection() {
        const selected = selectedNodeId ? cy.getElementById(selectedNodeId) : cy.nodes(".focus-root").first();
        const data = selected && !selected.empty() ? selected.data() : selectedContext.data || {};
        const url = grafanaUrlForNode(data);
        window.open(url, "_blank", "noopener,noreferrer");
        renderActionDraft("metrics");
    }

    function renderRelationshipCard(node) {
        const data = node.data();
        const neighbors = node.connectedEdges().connectedNodes().filter((item) => item.id() !== node.id());
        const neighborLabels = neighbors.slice(0, 8).map((item) => item.data("label") || item.data("name") || item.id());
        const pack = expansionPacks[node.id()];
        const fallbackSummary = `${data.kind || "resource"} expands into ${neighborLabels.length} nearby relationship card${neighborLabels.length === 1 ? "" : "s"}.`;
        const summary = (pack && pack.summary) ? pack.summary : fallbackSummary;
        relationshipCard.classList.remove("empty");
        relationshipCard.innerHTML = `
            <div class="orb">◎</div>
            <div>
                <strong>${escapeHtml(data.label || data.name || data.id || "Resource")}</strong>
                <span>${escapeHtml(summary)}</span>
                <div class="mini-edge-list">
                    ${neighborLabels.map((label) => `<span>${escapeHtml(label)}</span>`).join("") || "<span>No adjacent graph nodes</span>"}
                </div>
            </div>
        `;
    }

    function syntaxJson(value) {
        return escapeHtml(JSON.stringify(value, null, 2))
            .replace(/(&quot;[^&]+&quot;)(\s*:)/g, '<span class="code-key">$1</span>$2')
            .replace(/: (&quot;.*?&quot;)/g, ': <span class="code-string">$1</span>')
            .replace(/: (true|false|null)/g, ': <span class="code-bool">$1</span>')
            .replace(/: (-?\d+(?:\.\d+)?)/g, ': <span class="code-number">$1</span>');
    }

    function renderCode(data) {
        if (!codeView) return;
        codeView.innerHTML = `<code>${syntaxJson(data)}</code>`;
    }

    function escapeHtml(value) {
        return value.replace(/[&<>"']/g, (char) => ({
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#039;",
        })[char]);
    }

    cy.on("tap", "node", (event) => renderSelection(event.target));
    cy.on("cxttap", "node", (event) => {
        renderSelection(event.target);
        showNodePopover(event.target);
    });

    document.getElementById("beta-reset-view")?.addEventListener("click", () => {
        cy.elements().removeClass("dimmed");
        applyStaleVisibility();
        cy.fit(undefined, 45);
        if (nodePopover) nodePopover.hidden = true;
    });

    document.getElementById("beta-scope-one")?.addEventListener("click", () => applyScope(1));
    document.getElementById("beta-scope-two")?.addEventListener("click", () => applyScope(2));
    document.getElementById("beta-scope-all")?.addEventListener("click", () => applyScope("all"));

    document.getElementById("beta-hide-healthy")?.addEventListener("click", () => {
        cy.nodes().forEach((node) => {
            const data = node.data();
            const state = String(data.state || data.status || "").toLowerCase();
            if (["running", "success", "ok", "healthy"].includes(state)) node.addClass("dimmed");
        });
    });

    function isStaleNode(node) {
        const data = node.data();
        const state = String(data.state || data.status || data.kind || "").toLowerCase();
        const text = [
            data.id,
            data.label,
            data.kind,
            data.platform,
            data.role,
            data.last_seen,
        ].filter(Boolean).join(" ").toLowerCase();
        return ["stale", "offline", "inactive"].includes(state) || text.includes("stale") || text.includes("snapshot only");
    }

    function applyStaleVisibility() {
        const staleNodes = cy.nodes().filter((node) => {
            return isStaleNode(node);
        });
        const staleEdges = staleNodes.connectedEdges();
        if (showStale) {
            staleNodes.removeClass("hidden-stale");
            staleEdges.removeClass("hidden-stale");
        } else {
            staleNodes.addClass("hidden-stale");
            staleEdges.addClass("hidden-stale");
        }
    }

    applyStaleVisibility();

    document.getElementById("beta-toggle-stale")?.addEventListener("click", (event) => {
        showStale = !showStale;
        event.currentTarget.textContent = showStale ? "Hide stale" : "Show stale";
        applyStaleVisibility();
        cy.fit(cy.elements().not(".hidden-stale"), 60);
    });

    document.querySelectorAll("[data-popover-action]").forEach((button) => {
        button.addEventListener("click", () => {
            const action = String(button.dataset.popoverAction || "inspect");
            if (action === "expand") {
                const selected = selectedNodeId ? cy.getElementById(selectedNodeId) : cy.nodes(".focus-root").first();
                if (!selected || selected.empty() || !expandGraphPack(selected.id(), { force: true })) {
                    expandSelectedNeighborhood();
                }
                renderActionDraft("inspect");
            } else if (action === "open") {
                window.location.href = openCurrent.href || "/resources";
            } else if (action === "metrics") {
                openMetricsForSelection();
            } else {
                renderActionDraft("evidence");
                document.getElementById("beta-action-drawer")?.scrollIntoView({ behavior: "smooth", block: "center" });
            }
        });
    });

    function expandSelectedNeighborhood() {
        const selected = cy.nodes(".focus-root").first();
        if (!selected || selected.empty()) return;
        const nodes = selected.closedNeighborhood().nodes();
        const center = selected.position();
        const radius = 180;
        const count = Math.max(1, nodes.length - 1);
        let index = 0;
        nodes.forEach((node) => {
            if (node.id() === selected.id()) {
                node.animate({ position: center }, { duration: 220 });
                return;
            }
            const angle = (Math.PI * 2 * index) / count;
            index += 1;
            node.animate({
                position: {
                    x: center.x + Math.cos(angle) * radius,
                    y: center.y + Math.sin(angle) * radius,
                },
            }, { duration: 260 });
        });
        cy.animate({ fit: { eles: selected.closedNeighborhood(), padding: 60 } }, { duration: 260 });
    }

    function expandGraphPack(rootId, options = {}) {
        const root = cy.getElementById(rootId);
        const packId = resolveExpansionPackId(rootId, root);
        const pack = expansionPacks[packId];
        if (!pack || !root || root.empty()) return false;
        if (expandedPacks.has(rootId) && !options.force) {
            relayoutPack(rootId, packId);
            const scoped = root.closedNeighborhood().union(packNodeCollection(pack));
            cy.elements().addClass("dimmed").removeClass("focus-root focus-neighbor");
            scoped.removeClass("dimmed").addClass("focus-neighbor");
            root.removeClass("focus-neighbor").addClass("focus-root");
            cy.animate({ fit: { eles: scoped, padding: 72 } }, { duration: options.auto ? 210 : 300 });
            return true;
        }

        const layout = pack.layout || {};
        const rootPos = root.position();
        const start = Number(layout.start === undefined ? -80 : layout.start);
        const step = Number(layout.step === undefined ? 60 : layout.step);
        const distance = Number(layout.distance === undefined ? 210 : layout.distance);
        const addables = [];
        const newIds = [];

        pack.nodes.forEach((nodeData, index) => {
            if (cy.getElementById(nodeData.id).length) return;
            const y = start + (index * step);
            const xOffset = distance + Math.abs(y) * 0.35;
            const data = { ...nodeData };
            data.iconLabel = iconLabelFor(data);
            data.expandable = expansionPacks[data.id] ? "true" : "false";
            addables.push({
                group: "nodes",
                data,
                position: {
                    x: rootPos.x + xOffset,
                    y: rootPos.y + y,
                },
            });
            newIds.push(nodeData.id);
        });

        pack.edges.forEach(([source, target, relation]) => {
            const actualSource = source === packId ? rootId : source;
            const actualTarget = target === packId ? rootId : target;
            const edgeId = `edge:${actualSource}:${relation}:${actualTarget}`;
            if (cy.getElementById(edgeId).length) return;
            addables.push({
                group: "edges",
                data: { id: edgeId, source: actualSource, target: actualTarget, type: relation, label: relation },
            });
        });

        if (addables.length) {
            cy.add(addables);
        }
        relayoutPack(rootId, packId);
        applyStaleVisibility();

        expandedPacks.add(rootId);
        const scoped = root.closedNeighborhood().union(packNodeCollection(pack));
        cy.elements().addClass("dimmed").removeClass("focus-root focus-neighbor");
        scoped.removeClass("dimmed").addClass("focus-neighbor");
        root.removeClass("focus-neighbor").addClass("focus-root");
        cy.animate({ fit: { eles: scoped, padding: 72 } }, { duration: options.auto ? 210 : 300 });
        markExpandableNodes();
        return true;
    }

    function packNodeCollection(pack) {
        return cy.collection((pack.nodes || []).map((nodeData) => cy.getElementById(nodeData.id)));
    }

    function relayoutPack(rootId, packId = rootId) {
        const pack = expansionPacks[packId];
        const root = cy.getElementById(rootId);
        if (!pack || !root || root.empty()) return;
        const rootPos = root.position();
        const layout = pack.layout || {};
        const mode = String(layout.mode || "column").toLowerCase();
        const direction = String(layout.direction || "right").toLowerCase();
        if (mode === "fan") {
            const count = Math.max(1, (pack.nodes || []).length);
            const arc = Number(layout.arc === undefined ? 110 : layout.arc);
            const distance = Number(layout.distance === undefined ? 260 : layout.distance);
            const startAngle = -arc / 2;
            const stepAngle = count === 1 ? 0 : arc / (count - 1);
            const baseAngle = {
                right: 0,
                down: 90,
                left: 180,
                up: -90,
            }[direction] ?? 0;
            pack.nodes.forEach((nodeData, index) => {
                const node = cy.getElementById(nodeData.id);
                if (!node || node.empty()) return;
                const angle = (Math.PI / 180) * (baseAngle + startAngle + (stepAngle * index));
                node.animate({
                    position: {
                        x: rootPos.x + Math.cos(angle) * distance,
                        y: rootPos.y + Math.sin(angle) * distance,
                    },
                }, { duration: 260 });
            });
            return;
        }
        const start = Number(layout.start === undefined ? -80 : layout.start);
        const step = Number(layout.step === undefined ? 60 : layout.step);
        const distance = Number(layout.distance === undefined ? 210 : layout.distance);
        pack.nodes.forEach((nodeData, index) => {
            const node = cy.getElementById(nodeData.id);
            if (!node || node.empty()) return;
            const y = start + (index * step);
            const xOffset = distance + Math.abs(y) * 0.35;
            node.animate({ position: { x: rootPos.x + xOffset, y: rootPos.y + y } }, { duration: 260 });
        });
    }

    function iconLabelFor(data) {
        const icons = {
            firewall: "🛡",
            isp: "⌁",
            switch: "▦",
            bmc: "▣",
            host: "▤",
            cluster: "☁",
            vm: "◇",
            container: "▱",
            stack: "▥",
            evidence: "⌕",
            interface: "∿",
        };
        const type = String(data.type || "").toLowerCase();
        const plus = resolveExpansionPackId(data.id, { data: () => data }) ? "＋" : "";
        return `${icons[type] || "●"}${plus}
${data.label || data.id || ""}`;
    }

    function markExpandableNodes() {
        cy.nodes().forEach((node) => {
            const expandable = Boolean(resolveExpansionPackId(node.id(), node));
            node.data("expandable", expandable ? "true" : "false");
            node.data("iconLabel", iconLabelFor(node.data()));
        });
    }

    function resolveExpansionPackId(id, node) {
        if (expansionPacks[id]) return id;
        if (expansionAliases[id]) return expansionAliases[id];
        const data = node && typeof node.data === "function" ? node.data() : {};
        const haystack = [
            id,
            data.label,
            data.name,
            data.kind,
            data.platform,
            data.ip,
            data.role,
        ].filter(Boolean).join(" ").toLowerCase();
        if (
            haystack.includes("proxmox") ||
            haystack.includes("pve1") ||
            haystack.includes("192.168.1.9")
        ) {
            return "host:pve1";
        }
        if (haystack.includes("swarm1.lab.auzietek.com") || haystack.includes("swarm1")) {
            return "vm:swarm1.lab.auzietek.com";
        }
        if (haystack.includes("ns1.lab.auzietek.com") || haystack === "ns1" || haystack.includes("dns/nfs/pxe")) {
            return "vm:ns1.lab.auzietek.com";
        }
        return "";
    }

    cy.on("pan zoom resize", () => {
        const selected = cy.nodes(".focus-root").first();
        if (selected && !selected.empty() && nodePopover && !nodePopover.hidden) showNodePopover(selected);
    });

    cy.on("tap", (event) => {
        if (event.target === cy) {
            cy.elements().removeClass("dimmed focus-root focus-neighbor");
            if (nodePopover) nodePopover.hidden = true;
        }
    });

    function setActiveViewButton(mode) {
        document.querySelectorAll("[data-view-mode]").forEach((item) => {
            item.classList.toggle("active", item.dataset.viewMode === mode);
        });
    }

    function graphText(node) {
        const data = node.data();
        return Object.entries(data)
            .filter(([key, value]) => key !== "iconLabel" && value !== null && value !== undefined)
            .flatMap(([key, value]) => [key, String(value)])
            .join(" ")
            .toLowerCase();
    }

    function focusCollection(collection, options = {}) {
        if (!collection || collection.empty()) return false;
        cy.elements().removeClass("search-hit search-path dimmed focus-root focus-neighbor");
        cy.elements().addClass("dimmed");
        collection.removeClass("dimmed").addClass("focus-neighbor");
        collection.edges().addClass("search-path");
        if (options.markNodes) collection.nodes().addClass("search-hit");
        const root = options.rootId ? cy.getElementById(options.rootId) : collection.nodes().first();
        if (root && !root.empty()) {
            root.removeClass("focus-neighbor").addClass("focus-root search-hit");
            selectedNodeId = root.id();
            renderRelationshipCard(root);
            renderCode(root.data());
            title.textContent = root.data("label") || root.data("name") || root.id();
            kind.textContent = [root.data("kind"), root.data("status") || root.data("state")].filter(Boolean).join(" · ") || "resource";
        }
        cy.animate({ fit: { eles: collection, padding: options.padding || 88 } }, { duration: 280 });
        return true;
    }

    function expandUsefulTopology() {
        ["platform:hypervisors", "host:pve1", "host:server1", "host:server2", "edge:ipfire", "fabric:n2024"].forEach((id) => {
            if (cy.getElementById(id).length) expandGraphPack(id, { auto: true });
        });
    }

    function focusViewMode(mode) {
        setActiveViewButton(mode);
        if (nodePopover) nodePopover.hidden = true;
        applyStaleVisibility();

        if (mode === "topology") {
            cy.elements(".pipeline-story").addClass("hidden-pipeline");
            expandUsefulTopology();
            cy.elements().removeClass("search-hit search-path dimmed focus-root focus-neighbor");
            const roots = cy.nodes().filter((node) => {
                const text = graphText(node);
                return ["spectrum", "ipfire", "n2024", "ipmi", "ns1", "openstack", "hypervisors", "pve1", "server1", "server2"]
                    .some((needle) => text.includes(needle));
            });
            roots.addClass("search-hit");
            cy.animate({ fit: { eles: cy.elements().not(".hidden-stale"), padding: 54 } }, { duration: 280 });
            actionTitle.textContent = "Topology";
            actionCopy.textContent = "Topology mode shows the living lab fabric: edge, switch, IPMI, core services, and hypervisors.";
            actionPayload.innerHTML = `<code>${syntaxJson({ view: "topology", mode: "city", focus: "living lab fabric" })}</code>`;
            return;
        }

        if (mode === "pipelines") {
            expandUsefulTopology();
            ["host:server1", "host:server2", "host:pve1"].forEach((id) => {
                if (cy.getElementById(id).length) expandGraphPack(id, { auto: true });
            });
            cy.elements(".pipeline-story").removeClass("hidden-pipeline");
            const firstPipeline = document.querySelector(".pipeline-row");
            firstPipeline?.scrollIntoView({ behavior: "smooth", block: "center" });
            if (firstPipeline && !firstPipeline.classList.contains("expanded")) {
                firstPipeline.querySelector(".pipeline-row-main")?.click();
            }
            const pipelineNodes = cy.nodes().filter((node) => {
                const text = graphText(node);
                return node.hasClass("pipeline-story")
                    || text.includes("pipeline")
                    || text.includes("stage")
                    || text.includes("video")
                    || text.includes("pxe")
                    || text.includes("provision");
            });
            const context = pipelineNodes.union(pipelineNodes.connectedEdges()).union(pipelineNodes.connectedEdges().connectedNodes());
            focusCollection(context.not(".hidden-stale"), { rootId: "pipeline:baremetal-lab-reset", markNodes: true, padding: 72 });
            actionTitle.textContent = "Pipeline paths";
            actionCopy.textContent = "Pipeline mode reveals VIDEO pipelines as graph lanes: pipeline → stage bubbles → affected hosts, VMs, services, and evidence.";
            actionPayload.innerHTML = `<code>${syntaxJson({ view: "pipelines", story: "pipeline → stages → affected infrastructure", examples: ["10 VIDEO affects server1/OpenStack", "20 VIDEO affects server2/Proxmox", "50B affects ESXi swarm VMs"], next: "select a pipeline node or row to inspect/run/edit" })}</code>`;
            return;
        }

        if (mode === "edge") {
            cy.elements(".pipeline-story").addClass("hidden-pipeline");
            ["edge:ipfire", "fabric:n2024", "core:ns1", "host:pve1", "vm:pve-ipfire", "evidence:pve1-mac-adjacency"].forEach((id) => {
                if (cy.getElementById(id).length) expandGraphPack(id, { auto: true });
            });
            const edgeNodes = cy.nodes().filter((node) => {
                const text = graphText(node);
                return ["edge", "firewall", "ipfire", "nat", "switch", "n2024", "dhcp", "dns", "pxe", "gateway", "spectrum", "vmbr", "mac"]
                    .some((needle) => text.includes(needle));
            });
            const context = edgeNodes.union(edgeNodes.connectedEdges()).union(edgeNodes.connectedEdges().connectedNodes());
            focusCollection(context, { rootId: cy.getElementById("edge:ipfire").length ? "edge:ipfire" : "fabric:n2024", markNodes: true, padding: 92 });
            actionTitle.textContent = "Edge ownership";
            actionCopy.textContent = "Edge mode follows WAN, firewall, switch, DHCP/DNS/PXE, MAC evidence, and pve1 bridge relationships.";
            actionPayload.innerHTML = `<code>${syntaxJson({ view: "edge", focus: ["Spectrum", "IPFire", "N2024", "ns1", "pve1 bridges"], mode: "evidence-first" })}</code>`;
            return;
        }

        if (mode === "failures") {
            cy.elements(".pipeline-story").removeClass("hidden-pipeline");
            expandUsefulTopology();
            const failureNodes = cy.nodes().filter((node) => {
                const data = node.data();
                const state = String(data.state || data.status || "").toLowerCase();
                const text = graphText(node);
                return ["failed", "failure", "error", "blocked", "unreachable", "offline", "stale", "warning"].includes(state)
                    || text.includes("stale")
                    || text.includes("unreachable")
                    || text.includes("failed")
                    || text.includes("offline")
                    || text.includes("refresh error");
            });
            const context = failureNodes.union(failureNodes.connectedEdges()).union(failureNodes.connectedEdges().connectedNodes());
            focusCollection(context, { markNodes: true, padding: 102 });
            actionTitle.textContent = "Failures / stale evidence";
            actionCopy.textContent = "Failure mode dims healthy fabric and keeps stale, offline, unreachable, or warning evidence visible for triage.";
            actionPayload.innerHTML = `<code>${syntaxJson({ view: "failures", includes: ["failed", "offline", "stale", "unreachable", "warning"], purpose: "triage without hiding evidence" })}</code>`;
        }
    }

    document.querySelectorAll("[data-view-mode]").forEach((button) => {
        button.addEventListener("click", () => focusViewMode(button.dataset.viewMode || "topology"));
    });

    document.querySelectorAll(".pipeline-row-main").forEach((button) => {
        button.addEventListener("click", () => {
            const row = button.closest(".pipeline-row");
            if (!row) return;
            row.classList.toggle("expanded");
            const pipelineDataRaw = document.querySelector(".lower-grid")?.dataset.pipelines || "[]";
            try {
                const pipelines = JSON.parse(pipelineDataRaw);
                const selected = pipelines.find((item) => String(item.id) === String(row.dataset.pipelineId));
                if (selected) {
                    title.textContent = selected.name || selected.id;
                    selectedContext = {
                        type: "pipeline",
                        id: selected.id || "",
                        label: selected.name || selected.id || "Pipeline",
                        data: selected,
                    };
                    kind.textContent = `pipeline · ${selected.latest_run_status || selected.status || "catalog"}`;
                    facts.innerHTML = [
                        ["id", selected.id],
                        ["status", selected.status],
                        ["latest_run_status", selected.latest_run_status],
                        ["stage_count", selected.stage_count],
                    ].map(([key, value]) => `<div class="fact-item"><span>${escapeHtml(String(key))}</span>${escapeHtml(String(value || ""))}</div>`).join("");
                    openCurrent.href = `/pipelines?pipeline_id=${encodeURIComponent(selected.id || "")}`;
                    renderCode(selected);
                    relationshipCard.innerHTML = `
                        <div class="orb">⟡</div>
                        <div>
                            <strong>${escapeHtml(selected.name || selected.id || "Pipeline")}</strong>
                            <span>This pipeline expands into action bubbles; next pass can make these draggable fragments.</span>
                            <div class="mini-edge-list">
                                ${(selected.stages || []).slice(0, 8).map((stage) => `<span>${escapeHtml(stage.id || stage.action || "stage")}</span>`).join("")}
                            </div>
                        </div>
                    `;
                    renderActionDraft("inspect");
                }
            } catch (err) {
                console.warn("pipeline payload parse failed", err);
            }
        });
    });

    document.querySelectorAll(".fabric-card").forEach((button) => {
        button.addEventListener("click", () => {
            const raw = document.querySelector(".fabric-strip")?.dataset.fabric || "[]";
            try {
                const cards = JSON.parse(raw);
                const selected = cards.find((item) => String(item.id) === String(button.dataset.fabricId));
                if (!selected) return;
                const graphNode = cy.getElementById(String(selected.id || ""));
                if (graphNode && !graphNode.empty()) {
                    renderSelection(graphNode);
                    cy.animate({ fit: { eles: graphNode.closedNeighborhood(), padding: 92 } }, { duration: 320 });
                    return;
                }
                selectedContext = {
                    type: selected.kind || "fabric",
                    id: selected.id || "",
                    label: selected.label || selected.id || "Fabric item",
                    data: selected,
                };
                title.textContent = selected.label || selected.id;
                kind.textContent = `${selected.kind || "fabric"} · ${selected.state || "unknown"}`;
                facts.innerHTML = Object.entries(selected.facts || {})
                    .map(([key, value]) => `<div class="fact-item"><span>${escapeHtml(String(key))}</span>${escapeHtml(String(value || ""))}</div>`)
                    .join("");
                openCurrent.href = "/resources";
                renderCode(selected);
                relationshipCard.innerHTML = `
                    <div class="orb">✦</div>
                    <div>
                        <strong>${escapeHtml(selected.label || selected.id || "Fabric item")}</strong>
                        <span>${escapeHtml(selected.primary || "")} · ${escapeHtml(selected.secondary || "")}</span>
                        <div class="mini-edge-list">
                            ${(selected.actions || []).map((action) => `<span>${escapeHtml(action)}</span>`).join("")}
                        </div>
                    </div>
                `;
                renderActionDraft("inspect");
            } catch (err) {
                console.warn("fabric payload parse failed", err);
            }
        });
    });

    const fabricFilterInput = document.getElementById("beta-fabric-filter");
    const fabricFilterCount = document.getElementById("beta-fabric-filter-count");
    if (fabricFilterInput) {
        fabricFilterInput.addEventListener("input", () => filterFabricCards(fabricFilterInput.value || ""));
    }

    function filterFabricCards(query) {
        const cardsRaw = document.querySelector(".fabric-strip")?.dataset.fabric || "[]";
        const queryParts = String(query || "").toLowerCase().split(/\s+/).filter(Boolean);
        const buttons = Array.from(document.querySelectorAll(".fabric-card"));
        let fabricCards = [];
        try {
            fabricCards = JSON.parse(cardsRaw);
        } catch (err) {
            fabricCards = [];
        }
        let visible = 0;
        buttons.forEach((button) => {
            const card = fabricCards.find((item) => String(item.id) === String(button.dataset.fabricId)) || {};
            const haystack = [
                card.id,
                card.label,
                card.kind,
                card.state,
                card.primary,
                card.secondary,
                ...(card.actions || []),
                ...Object.entries(card.facts || {}).flat(),
            ].filter(Boolean).join(" ").toLowerCase();
            const matched = !queryParts.length || queryParts.every((part) => haystack.includes(part));
            button.classList.toggle("filtered-out", !matched);
            button.classList.toggle("filtered-hit", matched && queryParts.length > 0);
            if (matched) visible += 1;
        });
        if (fabricFilterCount) fabricFilterCount.textContent = `${visible}/${buttons.length}`;
        filterGraphNodes(queryParts);
    }

    function filterGraphNodes(queryParts) {
        cy.elements().removeClass("search-hit search-path dimmed focus-root focus-neighbor");
        if (!queryParts.length) {
            cy.fit(undefined, 45);
            return;
        }

        const preferredRootId = materializeSearchContext(queryParts.join(" "));
        const matches = cy.nodes().filter((node) => {
            const data = node.data();
            const haystack = Object.entries(data)
                .filter(([key, value]) => !["iconLabel"].includes(key) && value !== null && value !== undefined)
                .flatMap(([key, value]) => [key, String(value)])
                .join(" ")
                .toLowerCase();
            return queryParts.every((part) => haystack.includes(part));
        });

        let context = matches.union(matches.connectedEdges()).union(matches.connectedEdges().connectedNodes());
        const preferredRoot = preferredRootId ? cy.getElementById(preferredRootId) : cy.collection();
        if (preferredRoot && !preferredRoot.empty()) {
            context = context.union(preferredRoot).union(preferredRoot.closedNeighborhood());
        }
        if (!matches.length && (!preferredRoot || preferredRoot.empty())) return;
        cy.elements().addClass("dimmed");
        context.removeClass("dimmed");
        matches.addClass("search-hit");
        if (preferredRoot && !preferredRoot.empty()) preferredRoot.addClass("search-hit focus-root");
        context.edges().addClass("search-path");
        cy.animate({ fit: { eles: context, padding: 95 } }, { duration: 260 });
    }

    function materializeSearchContext(query) {
        const q = String(query || "").toLowerCase();
        let preferredRootId = "";
        if (q.includes("pve") || q.includes("proxmox") || q.includes("192.168.1.9") || q.includes("swarm") || q.includes("ns1")) {
            if (cy.getElementById("platform:hypervisors").length) expandGraphPack("platform:hypervisors", { auto: true });
            const pveNode = cy.getElementById("host:pve1");
            if (pveNode.length) expandGraphPack("host:pve1", { auto: true });
            if (q.includes("192.168.1.9") || q.includes("10.20.0.9") || q.includes("pve1")) preferredRootId = "host:pve1";
        }
        if (
            q.includes("192.168.1.9") ||
            q.includes("10.20.0.9") ||
            q.includes("fc:4d") ||
            q.includes("vmbr") ||
            q.includes("mac") ||
            q.includes("bridge")
        ) {
            if (cy.getElementById("platform:hypervisors").length) expandGraphPack("platform:hypervisors", { auto: true });
            if (cy.getElementById("host:pve1").length) expandGraphPack("host:pve1", { auto: true });
            if (cy.getElementById("evidence:pve1-mac-adjacency").length) expandGraphPack("evidence:pve1-mac-adjacency", { auto: true });
            preferredRootId = "evidence:pve1-mac-adjacency";
        }
        if (q.includes("swarm1") || q.includes("blackknight") || q.includes("bkc")) {
            if (cy.getElementById("vm:swarm1.lab.auzietek.com").length) expandGraphPack("vm:swarm1.lab.auzietek.com", { auto: true });
        }
        if (q.includes("ns1") || q.includes("dns") || q.includes("nfs") || q.includes("pxe")) {
            if (cy.getElementById("vm:ns1.lab.auzietek.com").length) expandGraphPack("vm:ns1.lab.auzietek.com", { auto: true });
        }
        return preferredRootId;
    }

    document.querySelectorAll("[data-code-mode]").forEach((button) => {
        button.addEventListener("click", () => {
            document.querySelectorAll("[data-code-mode]").forEach((item) => item.classList.remove("active"));
            button.classList.add("active");
            if (button.dataset.codeMode === "shell") {
                codeView.innerHTML = "<code><span class=\"code-key\"># template sketch</span>\\nbkc-ssh target -- inspect --json\\nbkc-pipeline run candidate --validate\\n</code>";
            }
        });
    });

    function renderActionDraft(action) {
        if (!actionTitle || !actionCopy || !actionPayload) return;
        const safeAction = action || "inspect";
        const target = selectedContext.id || "none";
        const label = selectedContext.label || "No target";
        const mode = ["run", "edit"].includes(safeAction) ? "requires-confirmation" : "read-only";
        actionTitle.textContent = `${capitalize(safeAction)} · ${label}`;
        actionCopy.textContent = actionCopyText(safeAction, selectedContext.type);
        actionPayload.innerHTML = `<code>${syntaxJson({
            action: safeAction,
            target_type: selectedContext.type,
            target_id: target,
            mode,
            transport: selectedContext.type === "pipeline" ? "pipeline-route" : "resource-graph-route",
            note: mode === "requires-confirmation" ? "UI draft only; backend action still uses current BKC route and permission checks." : "Safe preview/read path.",
        })}</code>`;
    }

    function actionCopyText(action, type) {
        const noun = type === "pipeline" ? "pipeline" : "resource";
        if (action === "edit") return `Open the focused ${noun} editor without losing graph context.`;
        if (action === "metrics") return `Open Grafana in a new tab with this ${noun}'s host, instance, and role as dashboard variables.`;
        if (action === "validate") return `Run or prepare a validation view for this ${noun}, with evidence linked back here.`;
        if (action === "evidence") return `Show logs, traces, fragments, and receipts that support this ${noun}'s current state.`;
        if (action === "run") return "Draft a run request. Destructive or mutating actions still need explicit confirmation.";
        return `Inspect this ${noun}'s observed state, desired state, relationships, and known-good fragments.`;
    }

    function capitalize(value) {
        return String(value || "").slice(0, 1).toUpperCase() + String(value || "").slice(1);
    }

    document.querySelectorAll(".bubble-actions button").forEach((button) => {
        button.addEventListener("click", () => {
            const action = String(button.textContent || "inspect").trim().toLowerCase();
            renderActionDraft(action);
        });
    });

    document.getElementById("beta-command-button")?.addEventListener("click", () => {
        renderActionDraft(selectedContext.type === "none" ? "inspect" : "validate");
        document.getElementById("beta-action-drawer")?.scrollIntoView({ behavior: "smooth", block: "center" });
    });

    function enableCardDrag(card) {
        let dragging = false;
        let startX = 0;
        let startY = 0;
        let offsetX = 0;
        let offsetY = 0;
        card.addEventListener("pointerdown", (event) => {
            dragging = true;
            startX = event.clientX - offsetX;
            startY = event.clientY - offsetY;
            card.setPointerCapture(event.pointerId);
            card.classList.add("is-dragging");
        });
        card.addEventListener("pointermove", (event) => {
            if (!dragging) return;
            offsetX = event.clientX - startX;
            offsetY = event.clientY - startY;
            card.style.transform = `translate(${offsetX}px, ${offsetY}px)`;
        });
        card.addEventListener("pointerup", (event) => {
            dragging = false;
            card.releasePointerCapture(event.pointerId);
            card.classList.remove("is-dragging");
        });
    }

    if (relationshipCard) enableCardDrag(relationshipCard);
})();
