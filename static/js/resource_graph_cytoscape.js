(function () {
  "use strict";

  const DEFAULT_STATUS_COLORS = {
    success: "#22c55e",
    failed: "#ef4444",
    running: "#f59e0b",
    inactive: "#64748b",
  };

  const DEFAULT_EDGE_COLORS = {
    ssh: "#38bdf8",
    dependency: "#64748b",
    pipeline_flow: "#a78bfa",
  };

  function debounce(callback, waitMs) {
    let timeoutId = null;
    return function debounced() {
      const args = arguments;
      window.clearTimeout(timeoutId);
      timeoutId = window.setTimeout(function () {
        callback.apply(null, args);
      }, waitMs);
    };
  }

  function csrfHeader() {
    const token = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content");
    return token ? { "X-CSRFToken": token } : {};
  }

  function normalizeElements(elements) {
    if (!elements || typeof elements !== "object") {
      return [];
    }
    return []
      .concat(Array.isArray(elements.nodes) ? elements.nodes : [])
      .concat(Array.isArray(elements.edges) ? elements.edges : []);
  }

  async function fetchGraphElements(endpoint, headers) {
    const response = await window.fetch(endpoint, {
      method: "GET",
      headers: Object.assign({ Accept: "application/json" }, headers || {}),
      credentials: "same-origin",
    });
    if (!response.ok) {
      throw new Error(`Graph request failed with HTTP ${response.status}`);
    }
    return response.json();
  }

  function createStyle() {
    return [
      {
        selector: "node",
        style: {
          shape: "round-rectangle",
          width: "label",
          height: 48,
          padding: "14px",
          "background-color": "#1e293b",
          "border-width": 1.5,
          "border-color": "rgba(226, 232, 240, 0.35)",
          color: "#f8fafc",
          label: "data(label)",
          "font-size": 12,
          "font-weight": 700,
          "text-valign": "center",
          "text-halign": "center",
          "text-wrap": "wrap",
          "text-max-width": 150,
          "overlay-padding": 6,
          "overlay-opacity": 0,
          "transition-property": "background-color, border-color, border-width",
          "transition-duration": "180ms",
        },
      },
      {
        selector: 'node[type = "pipeline"]',
        style: {
          "background-color": "#facc15",
          "border-color": "rgba(254, 240, 138, 0.88)",
          color: "#1f2937",
          height: 54,
          "text-max-width": 190,
        },
      },
      {
        selector: 'node[type = "stage"]',
        style: {
          "background-color": "#fde68a",
          "border-color": "rgba(245, 158, 11, 0.82)",
          color: "#1f2937",
          height: 46,
          "font-size": 11,
          "text-max-width": 170,
        },
      },
      {
        selector: 'node[type = "host"]',
        style: {
          shape: "round-rectangle",
          "background-color": "#86efac",
          "border-color": "rgba(187, 247, 208, 0.86)",
          color: "#052e16",
          height: 50,
        },
      },
      {
        selector: 'node[type = "vm"], node[type = "container"]',
        style: {
          "background-color": "#4ade80",
          "border-color": "rgba(187, 247, 208, 0.72)",
          color: "#052e16",
        },
      },
      {
        selector: ":parent",
        style: {
          "background-opacity": 0.12,
          "background-color": "#0f172a",
          "border-color": "rgba(56, 189, 248, 0.36)",
          "border-width": 2,
          padding: "26px",
          "text-valign": "top",
          "text-halign": "center",
        },
      },
      {
        selector: 'node[status = "success"]',
        style: {
          "border-color": "rgba(134, 239, 172, 0.72)",
        },
      },
      {
        selector: 'node[status = "failed"]',
        style: {
          "background-color": DEFAULT_STATUS_COLORS.failed,
          "border-color": "rgba(252, 165, 165, 0.82)",
        },
      },
      {
        selector: 'node[status = "running"]',
        style: {
          "background-color": DEFAULT_STATUS_COLORS.running,
          "border-color": "rgba(253, 230, 138, 0.86)",
        },
      },
      {
        selector: 'node[status = "inactive"]',
        style: {
          "background-color": DEFAULT_STATUS_COLORS.inactive,
          "border-color": "rgba(148, 163, 184, 0.46)",
          color: "#e2e8f0",
          opacity: 0.56,
        },
      },
      {
        selector: "edge",
        style: {
          width: 2,
          "curve-style": "unbundled-bezier",
          "control-point-distance": 36,
          "control-point-weight": 0.5,
          "target-arrow-shape": "triangle",
          "line-color": DEFAULT_EDGE_COLORS.dependency,
          "target-arrow-color": DEFAULT_EDGE_COLORS.dependency,
          "arrow-scale": 1.05,
          opacity: 0.78,
          "transition-property": "line-color, target-arrow-color, opacity",
          "transition-duration": "160ms",
        },
      },
      {
        selector: 'edge[type = "ssh"]',
        style: {
          "line-color": DEFAULT_EDGE_COLORS.ssh,
          "target-arrow-color": DEFAULT_EDGE_COLORS.ssh,
          "line-style": "dashed",
        },
      },
      {
        selector: 'edge[type = "pipeline_flow"]',
        style: {
          "line-color": DEFAULT_EDGE_COLORS.pipeline_flow,
          "target-arrow-color": DEFAULT_EDGE_COLORS.pipeline_flow,
          width: 3,
          "curve-style": "taxi",
          "taxi-direction": "rightward",
          "taxi-turn": 36,
        },
      },
      {
        selector: "node:selected",
        style: {
          "border-color": "#e2e8f0",
          "border-width": 4,
        },
      },
      {
        selector: "edge:selected",
        style: {
          opacity: 1,
          width: 4,
        },
      },
    ];
  }

  function hasPipelineFlow(elements) {
    return elements.some(function (element) {
      return element.data && element.data.source && element.data.type === "pipeline_flow";
    });
  }

  function statusSortValue(status) {
    const normalized = String(status || "").toLowerCase();
    if (normalized === "running") {
      return 0;
    }
    if (normalized === "success") {
      return 1;
    }
    if (normalized === "failed") {
      return 2;
    }
    if (normalized === "inactive") {
      return 3;
    }
    return 4;
  }

  function applyOwnershipPositions(nodes, startY) {
    const inventoryNodes = nodes.filter(function (node) {
      return !Number.isFinite(Number(node.data.storyRank));
    });
    const nodesById = new Map();
    inventoryNodes.forEach(function (node) {
      nodesById.set(String(node.data.id), node);
    });

    const groups = new Map();
    const loose = [];
    inventoryNodes.forEach(function (node) {
      const parentId = String(node.data.parent || "");
      if (parentId && nodesById.has(parentId)) {
        const list = groups.get(parentId) || [];
        list.push(node);
        groups.set(parentId, list);
        return;
      }
      if (!groups.has(String(node.data.id))) {
        groups.set(String(node.data.id), []);
      }
      loose.push(node);
    });

    const orderedParents = Array.from(groups.keys()).sort(function (a, b) {
      const nodeA = nodesById.get(a);
      const nodeB = nodesById.get(b);
      const labelA = nodeA?.data.label || a;
      const labelB = nodeB?.data.label || b;
      if (String(labelA).toLowerCase() === "pve") {
        return -1;
      }
      if (String(labelB).toLowerCase() === "pve") {
        return 1;
      }
      return String(labelA).localeCompare(String(labelB));
    });

    orderedParents.forEach(function (parentId, groupIndex) {
      const parent = nodesById.get(parentId);
      const children = (groups.get(parentId) || []).sort(function (a, b) {
        const stateOrder = statusSortValue(a.data.status) - statusSortValue(b.data.status);
        if (stateOrder !== 0) {
          return stateOrder;
        }
        return String(a.data.label || a.data.id).localeCompare(String(b.data.label || b.data.id));
      });
      const top = startY + groupIndex * 310;
      if (parent) {
        parent.position = { x: 150, y: top };
      }
      children.forEach(function (child, index) {
        const col = index % 4;
        const row = Math.floor(index / 4);
        child.position = {
          x: 340 + col * 220,
          y: top - 72 + row * 96,
        };
      });
    });

    loose.filter(function (node) {
      return !String(node.data.parent || "") && !node.position;
    }).sort(function (a, b) {
      return String(a.data.label || a.data.id).localeCompare(String(b.data.label || b.data.id));
    }).forEach(function (node, index) {
      node.position = {
        x: 120 + (index % 5) * 210,
        y: startY + orderedParents.length * 310 + Math.floor(index / 5) * 96,
      };
    });
  }

  function applyFlowPositions(elements) {
    const nodes = elements.filter(function (element) {
      return element.data && element.data.id && !element.data.source;
    });
    const storyNodes = nodes.filter(function (node) {
      return Number.isFinite(Number(node.data.storyRank));
    });
    if (storyNodes.length) {
      const groups = new Map();
      storyNodes.forEach(function (node) {
        const groupId = String(node.data.parentPipeline || node.data.id || "");
        const list = groups.get(groupId) || [];
        list.push(node);
        groups.set(groupId, list);
      });
      const sortedGroups = Array.from(groups.keys()).sort(function (a, b) {
        const labelA = groups.get(a).find(function (node) {
          return String(node.data.id) === a;
        })?.data.label || a;
        const labelB = groups.get(b).find(function (node) {
          return String(node.data.id) === b;
        })?.data.label || b;
        return String(labelA).localeCompare(String(labelB));
      });
      sortedGroups.forEach(function (groupId, groupIndex) {
        const columns = new Map();
        groups.get(groupId).forEach(function (node) {
          const rank = Number(node.data.storyRank || 0);
          const list = columns.get(rank) || [];
          list.push(node);
          columns.set(rank, list);
        });
        const groupTop = 130 + groupIndex * 300;
        Array.from(columns.keys()).sort(function (a, b) {
          return a - b;
        }).forEach(function (rank) {
          const list = columns.get(rank).sort(function (a, b) {
            const laneA = Number(a.data.storyLane || 0);
            const laneB = Number(b.data.storyLane || 0);
            if (laneA !== laneB) {
              return laneA - laneB;
            }
            return String(a.data.label || a.data.id).localeCompare(String(b.data.label || b.data.id));
          });
          const laneCounts = new Map();
          list.forEach(function (node) {
            const lane = Number(node.data.storyLane || 0);
            const count = laneCounts.get(lane) || 0;
            laneCounts.set(lane, count + 1);
            node.position = {
              x: 120 + rank * 260,
              y: groupTop + lane * 82 + count * 68,
            };
          });
        });
      });
      applyOwnershipPositions(nodes, 190 + sortedGroups.length * 300);
      return;
    }
    const edges = elements.filter(function (element) {
      return element.data && element.data.source && element.data.target;
    });
    const incoming = new Map();
    const outgoing = new Map();
    nodes.forEach(function (node) {
      incoming.set(node.data.id, 0);
      outgoing.set(node.data.id, []);
    });
    edges.forEach(function (edge) {
      const source = edge.data.source;
      const target = edge.data.target;
      if (!outgoing.has(source) || !incoming.has(target)) {
        return;
      }
      outgoing.get(source).push(target);
      incoming.set(target, incoming.get(target) + 1);
    });
    const queue = nodes
      .filter(function (node) {
        return incoming.get(node.data.id) === 0;
      })
      .map(function (node) {
        return node.data.id;
      });
    const rank = new Map(queue.map(function (id) {
      return [id, 0];
    }));
    while (queue.length) {
      const source = queue.shift();
      const nextRank = (rank.get(source) || 0) + 1;
      outgoing.get(source).forEach(function (target) {
        rank.set(target, Math.max(rank.get(target) || 0, nextRank));
        incoming.set(target, incoming.get(target) - 1);
        if (incoming.get(target) === 0) {
          queue.push(target);
        }
      });
    }
    const lanes = new Map();
    nodes.forEach(function (node) {
      const lane = rank.get(node.data.id) || 0;
      const list = lanes.get(lane) || [];
      list.push(node);
      lanes.set(lane, list);
    });
    Array.from(lanes.keys()).sort(function (a, b) {
      return a - b;
    }).forEach(function (lane) {
      const list = lanes.get(lane).sort(function (a, b) {
        return String(a.data.label || a.data.id).localeCompare(String(b.data.label || b.data.id));
      });
      const offset = ((list.length - 1) * 96) / 2;
      list.forEach(function (node, index) {
        node.position = {
          x: 120 + lane * 260,
          y: 120 + index * 112 - offset,
        };
      });
    });
    applyOwnershipPositions(nodes, 160 + lanes.size * 125);
  }

  function createLayout(elements, options) {
    const hasSavedPositions = elements.some(function (element) {
      return element.position && Number.isFinite(Number(element.position.x)) && Number.isFinite(Number(element.position.y));
    });
    if (options.flowLayout || (options.autoFlowLayout && hasPipelineFlow(elements))) {
      applyFlowPositions(elements);
      return { name: "preset", fit: true, padding: 64, animate: true };
    }
    if (hasSavedPositions) {
      return { name: "preset", fit: true, padding: 48, animate: true };
    }
    return {
      name: "cose",
      animate: true,
      fit: true,
      padding: 48,
      nodeRepulsion: function () {
        return 8000;
      },
      idealEdgeLength: function () {
        return 140;
      },
      edgeElasticity: function () {
        return 0.12;
      },
      gravity: 0.18,
      numIter: 1600,
    };
  }

  function runFlowLayout(cy) {
    const elements = cy.elements().map(function (element) {
      return element.json();
    });
    applyFlowPositions(elements);
    elements.forEach(function (element) {
      if (!element.position || !element.data || !element.data.id) {
        return;
      }
      const node = cy.getElementById(element.data.id);
      if (node.length) {
        node.animate({ position: element.position }, { duration: 360 });
      }
    });
    window.setTimeout(function () {
      cy.fit(undefined, 64);
    }, 380);
  }

  function runMapLayout(cy) {
    cy.layout({
      name: "cose",
      animate: true,
      fit: true,
      padding: 48,
      nodeRepulsion: function () {
        return 8000;
      },
      idealEdgeLength: function () {
        return 140;
      },
      edgeElasticity: function () {
        return 0.12;
      },
      gravity: 0.18,
      numIter: 1200,
    }).run();
  }

  function installLayoutControls(cy, options) {
    if (!options.layoutControlSelector) {
      return;
    }
    const buttons = Array.from(document.querySelectorAll(options.layoutControlSelector));
    function setActive(mode) {
      buttons.forEach(function (button) {
        button.classList.toggle("active", button.getAttribute("data-cy-layout") === mode);
      });
    }
    setActive(options.flowLayout || options.autoFlowLayout ? "flow" : "map");
    buttons.forEach(function (button) {
      button.addEventListener("click", function () {
        const mode = button.getAttribute("data-cy-layout");
        setActive(mode);
        if (mode === "flow") {
          runFlowLayout(cy);
        } else {
          runMapLayout(cy);
        }
      });
    });
  }

  function fitGraph(cy, padding) {
    const graphPadding = Number.isFinite(Number(padding)) ? Number(padding) : 72;
    const visibleElements = cy.elements().filter(function (element) {
      return element.visible();
    });
    const target = visibleElements.length ? visibleElements : cy.elements();
    cy.stop();
    cy.fit(target, graphPadding);
    if (cy.zoom() > 1.15) {
      cy.zoom({
        level: 1.15,
        renderedPosition: {
          x: cy.width() / 2,
          y: cy.height() / 2,
        },
      });
      cy.center(target);
    }
  }

  function installViewportControls(cy, options) {
    if (!options.viewportControlSelector) {
      return;
    }
    document.querySelectorAll(options.viewportControlSelector).forEach(function (button) {
      button.addEventListener("click", function () {
        const action = button.getAttribute("data-cy-viewport");
        if (action === "zoom-in" || action === "zoom-out") {
          const multiplier = action === "zoom-in" ? 1.18 : 0.84;
          cy.zoom({
            level: cy.zoom() * multiplier,
            renderedPosition: {
              x: cy.width() / 2,
              y: cy.height() / 2,
            },
          });
          return;
        }
        fitGraph(cy, options.fitPadding);
      });
    });
  }

  function nodeMatchesText(node, query) {
    if (!query) {
      return true;
    }
    const haystack = [
      node.id(),
      node.data("label"),
      node.data("type"),
      node.data("status"),
    ].join(" ").toLowerCase();
    return haystack.includes(query);
  }

  function visibleNeighborhood(cy, nodeId) {
    const root = cy.getElementById(nodeId);
    if (!root.length) {
      return cy.collection();
    }
    return root.union(root.neighborhood()).union(root.parent()).union(root.children());
  }

  function visiblePipelineStory(cy, nodeId) {
    const root = cy.getElementById(nodeId);
    if (!root.length) {
      return cy.collection();
    }
    const pipelineId = root.data("type") === "stage" ? root.data("parentPipeline") : nodeId;
    if (!pipelineId) {
      return cy.collection();
    }
    const pipeline = cy.getElementById(String(pipelineId));
    const stages = cy.nodes('[type = "stage"]').filter(function (node) {
      return node.data("parentPipeline") === pipelineId;
    });
    const storyNodes = pipeline.union(stages);
    return storyNodes.union(storyNodes.connectedEdges().filter(function (edge) {
      return edge.data("type") === "pipeline_flow";
    }));
  }

  function applyGraphFilter(cy, options, mode, query) {
    const normalizedMode = mode || "selected";
    const normalizedQuery = String(query || "").trim().toLowerCase();
    const selectedId = String(options.selectedNodeId || "").trim();
    let visible = cy.nodes();

    if (normalizedMode === "selected" && selectedId) {
      const selectedNode = cy.getElementById(selectedId);
      visible = selectedNode.length && ["pipeline", "stage"].includes(String(selectedNode.data("type")))
        ? visiblePipelineStory(cy, selectedId)
        : visibleNeighborhood(cy, selectedId);
      if (!visible.length) {
        visible = cy.nodes('node[type = "host"], node[type = "vm"], node[type = "container"]');
      }
    } else if (normalizedMode === "pipeline") {
      visible = cy.nodes('[type = "pipeline"], node[type = "stage"]');
    } else if (normalizedMode === "compute") {
      visible = cy.nodes('node[type = "host"], node[type = "vm"], node[type = "container"]');
    }

    if (normalizedQuery) {
      visible = visible.filter(function (node) {
        return nodeMatchesText(node, normalizedQuery);
      });
    }

    visible = visible.union(visible.parents());
    cy.batch(function () {
      cy.elements().hide();
      visible.show();
      visible.connectedEdges().filter(function (edge) {
        return edge.source().visible() && edge.target().visible();
      }).show();
    });

    if (visible.length) {
      window.setTimeout(function () {
        fitGraph(cy, options.fitPadding);
      }, 80);
    }
  }

  function installFilterControls(cy, options) {
    if (!options.filterControlSelector) {
      return;
    }
    const buttons = Array.from(document.querySelectorAll(options.filterControlSelector));
    const search = options.filterSearchSelector ? document.querySelector(options.filterSearchSelector) : null;
    let mode = options.defaultFilterMode || (options.selectedNodeId ? "selected" : "compute");
    if (mode === "selected" && (!options.selectedNodeId || !cy.getElementById(String(options.selectedNodeId)).length)) {
      mode = "compute";
    }

    function setActive(nextMode) {
      buttons.forEach(function (button) {
        button.classList.toggle("active", button.getAttribute("data-cy-filter") === nextMode);
      });
    }

    function refresh() {
      setActive(mode);
      applyGraphFilter(cy, options, mode, search ? search.value : "");
    }

    buttons.forEach(function (button) {
      button.addEventListener("click", function () {
        mode = button.getAttribute("data-cy-filter") || "all";
        refresh();
      });
    });

    if (search) {
      search.addEventListener("input", debounce(refresh, 180));
    }
    refresh();
  }

  function installRunningPulse(cy) {
    let bright = false;
    return window.setInterval(function () {
      bright = !bright;
      cy.nodes('[status = "running"]').animate(
        {
          style: {
            "background-color": bright ? "#fbbf24" : DEFAULT_STATUS_COLORS.running,
            "border-width": bright ? 4 : 2,
          },
        },
        { duration: 520 }
      );
    }, 700);
  }

  function installPositionSave(cy, options) {
    const endpoint = options.savePositionsEndpoint || "/api/v1/tenant/graph/save-positions";
    const pending = new Map();
    const sendPositions = debounce(async function () {
      const positions = Array.from(pending.values());
      pending.clear();
      if (!positions.length) {
        return;
      }
      try {
        const response = await window.fetch(endpoint, {
          method: "POST",
          headers: Object.assign(
            { "Content-Type": "application/json", Accept: "application/json" },
            csrfHeader(),
            options.fetchHeaders || {}
          ),
          credentials: "same-origin",
          body: JSON.stringify({ positions: positions }),
        });
        if (!response.ok) {
          throw new Error(`Position save failed with HTTP ${response.status}`);
        }
      } catch (error) {
        console.error("BKC graph position save failed", error);
      }
    }, options.positionSaveDebounceMs || 500);

    cy.on("free", "node", function (event) {
      const node = event.target;
      const position = node.position();
      pending.set(node.id(), {
        id: node.id(),
        x: position.x,
        y: position.y,
      });
      sendPositions();
    });
  }

  function contextMenuItemsForNode(node) {
    const type = String(node.data("type") || "").toLowerCase();
    if (type === "host") {
      return [
        { id: "fast-ssh-check", label: "Run Fast SSH Check" },
        { id: "deploy-ansible-playbook", label: "Deploy Ansible Playbook" },
        { id: "view-metrics", label: "View Metrics" },
      ];
    }
    if (type === "pipeline") {
      return [
        { id: "trigger-run", label: "Trigger Run" },
        { id: "view-stage-history", label: "View Stage History" },
      ];
    }
    return [];
  }

  async function defaultContextAction(actionId, nodeId, nodeData, options) {
    const endpoint = options.contextActionEndpoint || "/resources/graph/context-action";
    const response = await window.fetch(endpoint, {
      method: "POST",
      headers: Object.assign(
        { "Content-Type": "application/json", Accept: "application/json" },
        csrfHeader(),
        options.fetchHeaders || {}
      ),
      credentials: "same-origin",
      body: JSON.stringify({
        action: actionId,
        node_id: nodeId,
        node_type: nodeData.type || "",
      }),
    });
    const body = await response.json().catch(function () {
      return {};
    });
    if (!response.ok) {
      throw new Error(body.error || `Context action failed with HTTP ${response.status}`);
    }
    if (body.redirect_url) {
      window.location.href = body.redirect_url;
    }
    return body;
  }

  function installContextMenu(cy, options) {
    const menu = document.createElement("div");
    menu.className = "cy-context-menu";
    menu.setAttribute("role", "menu");
    menu.hidden = true;
    document.body.appendChild(menu);

    function hideMenu() {
      menu.hidden = true;
      menu.innerHTML = "";
    }

    function placeMenu(x, y) {
      menu.style.left = `${x}px`;
      menu.style.top = `${y}px`;
      menu.hidden = false;
      const rect = menu.getBoundingClientRect();
      const padding = 10;
      const adjustedX = Math.min(x, window.innerWidth - rect.width - padding);
      const adjustedY = Math.min(y, window.innerHeight - rect.height - padding);
      menu.style.left = `${Math.max(padding, adjustedX)}px`;
      menu.style.top = `${Math.max(padding, adjustedY)}px`;
    }

    function runAction(actionId, node) {
      const nodeId = node.id();
      const nodeData = node.data();
      const customHandler = options.onContextMenuAction || window.handleResourceGraphContextAction;
      hideMenu();
      if (typeof customHandler === "function") {
        customHandler(actionId, nodeId, nodeData);
        return;
      }
      defaultContextAction(actionId, nodeId, nodeData, options).catch(function (error) {
        console.error("BKC graph context action failed", error);
      });
    }

    cy.on("cxttap", "node", function (event) {
      if (event.originalEvent && typeof event.originalEvent.preventDefault === "function") {
        event.originalEvent.preventDefault();
      }
      const node = event.target;
      const items = contextMenuItemsForNode(node);
      if (!items.length) {
        hideMenu();
        return;
      }
      menu.innerHTML = "";
      items.forEach(function (item) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "cy-context-menu-item";
        button.textContent = item.label;
        button.addEventListener("click", function () {
          runAction(item.id, node);
        });
        menu.appendChild(button);
      });
      const original = event.originalEvent || {};
      const rect = cy.container().getBoundingClientRect();
      const renderedPosition = event.renderedPosition || { x: rect.width / 2, y: rect.height / 2 };
      const x = Number.isFinite(Number(original.clientX)) ? Number(original.clientX) : rect.left + renderedPosition.x;
      const y = Number.isFinite(Number(original.clientY)) ? Number(original.clientY) : rect.top + renderedPosition.y;
      placeMenu(x, y);
    });

    cy.container().addEventListener("contextmenu", function (event) {
      event.preventDefault();
    });

    cy.on("tap pan zoom drag", hideMenu);
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        hideMenu();
      }
    });
    document.addEventListener("click", function (event) {
      if (!menu.hidden && !menu.contains(event.target)) {
        hideMenu();
      }
    });
    cy.on("destroy", function () {
      menu.remove();
    });
  }

  async function initBkcCytoscapeGraph(options) {
    const config = Object.assign(
      {
        containerSelector: "#cy",
        boxSelectionEnabled: false,
        minZoom: 0.18,
        maxZoom: 3,
        wheelSensitivity: 0.22,
      },
      options || {}
    );
    const container = document.querySelector(config.containerSelector);
    if (!container) {
      throw new Error(`Cytoscape container ${config.containerSelector} was not found`);
    }
    if (!window.cytoscape) {
      throw new Error("Cytoscape.js is not loaded");
    }

    const rawElements = config.elements || (config.graphEndpoint ? await fetchGraphElements(config.graphEndpoint, config.fetchHeaders) : {});
    const elements = normalizeElements(rawElements);
    const cy = window.cytoscape({
      container: container,
      elements: elements,
      style: createStyle(),
      layout: createLayout(elements, config),
      userPanningEnabled: true,
      userZoomingEnabled: true,
      boxSelectionEnabled: config.boxSelectionEnabled,
      wheelSensitivity: config.wheelSensitivity,
      autoungrabify: false,
      minZoom: config.minZoom,
      maxZoom: config.maxZoom,
    });

    cy.on("tap", "node", function (event) {
      const nodeId = event.target.id();
      const drilldown = config.openResourceDrilldown || window.openResourceDrilldown;
      if (typeof drilldown === "function") {
        drilldown(nodeId, event.target.data());
      }
    });

    installPositionSave(cy, config);
    installContextMenu(cy, config);
    installLayoutControls(cy, config);
    installViewportControls(cy, config);
    installFilterControls(cy, config);
    cy.ready(function () {
      window.setTimeout(function () {
        fitGraph(cy, config.fitPadding);
      }, 120);
    });
    const pulseTimer = installRunningPulse(cy);
    cy.on("destroy", function () {
      window.clearInterval(pulseTimer);
    });
    window.bkcResourceGraphCy = cy;
    return cy;
  }

  window.initBkcCytoscapeGraph = initBkcCytoscapeGraph;
})();
