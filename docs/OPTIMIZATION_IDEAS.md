# OpenClaw WS Router: Optimization Roadmap

This document outlines identified performance bottlenecks and proposed solutions to bring `ws_router` latency and user experience on par with native OpenClaw channels.

## 1. Response Latency & Perception

### The Problem
Currently, `ws_router` wait for the **entire** message to be generated and aggregated before displaying anything other than a static "Thinking..." placeholder. In long sessions (20k+ tokens), LLM generation can take 30-60 seconds, leading to a poor user experience.

### Proposed Solution: Partial Streaming
Implement "Edit-in-place" streaming similar to native OpenClaw Mattermost plugin.
- **Mechanism**:
    1. Catch `state=partial` (or `delta`) events in `OpenClawClient`.
    2. Pass these partial strings to `WSConnectionManager`.
    3. Use a **Throttled Update** mechanism (e.g., once every 1000ms) to call Mattermost `posts.patch` API.
    4. Update the existing "Thinking..." post with current partial text.
- **Benefit**: Users see the response starting within 1-3 seconds, significantly improving perceived performance.

## 2. Context Management & Token Bloat

### The Problem
OpenClaw Gateway currently manages the entire Mattermost thread as a single continuous session. Over time, the context grows (25k+ tokens), causing:
1. Higher costs.
2. Slower LLM "Time to First Token".
3. Frequent provider errors (Context Window exceeded).

### Proposed Solution: Context Pruning/Compaction
While managed by the Gateway, the Router can facilitate:
- **Session Reset**: Implement a `/clear` or `/reset` command in the router that signals the Gateway to start a fresh session key for the current channel.
- **Gateway Tuning**: Adjust `compactionCheckpointCount` or context window limits in the Gateway configuration (likely in the `gateway` repo).

## 3. Infrastructure Overhead

### The Problem
The current message flow is: `Gateway -> (WebSocket) -> ws_router -> (HTTP) -> Mattermost`.
The extra WebSocket hop and Python's async overhead add ~200-500ms of unavoidable latency compared to the native TS/JS implementation inside the Gateway.

### Proposed Solution: Direct Streaming Bypassing Aggregator
- **Current**: Fallback mechanism waits 450ms + Aggregator waits 150ms.
- **Optimization**: For `state=final` messages, if it's the only candidate, resolve the response `Future` immediately without entering the `ClawAggregator` debounce loop. (Partially implemented in Checkpoint 9).

## 4. BigQuery & Tool Retries

### The Problem
Frequent `403 Forbidden` errors in BigQuery lead to automatic retries within the agent, adding 10-20 seconds per tool call.

### Proposed Solution: Permission & Connection Audit
- Audit BigQuery service account permissions.
- Check if the VPN/Proxy used by MCP servers is causing transient timeouts that the agent interprets as failures.

---
*Created: 2026-04-23*
