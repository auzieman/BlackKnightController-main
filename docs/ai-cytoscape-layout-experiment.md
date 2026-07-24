# AI Cytoscape Layout Experiment

This is a side experiment, not a dependency for the filming pipeline.

The resource graph page has an `AI Layout` button that asks local Ollama on
Server1 for Cytoscape node positions. BKC validates the response before it can
touch saved graph positions:

- the model may only use real Cytoscape node IDs;
- coordinates are clamped to the requested canvas;
- at least three model-proposed anchors are required;
- missing node positions are filled by a deterministic fallback grid;
- saved positions still go through the existing `graph_positions` table.

Current model:

- `qwen2.5-coder:1.5b`

Current route:

- `POST /resources/graph/ai-layout`

This keeps the model in the role we want: suggestive, bounded, and replaceable.
