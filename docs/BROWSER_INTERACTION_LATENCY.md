# Responsive shared-browser mouse control

The assisted browser's live interaction path now prioritizes mouse input and visible feedback.

## What changed

- Authenticated live input, status, frame and screenshot requests resolve the existing workspace directly. They retain authentication, CSRF checks and session ownership without running workspace migrations or starting pipeline runners per request.
- Complete JPEG frame bodies are decoded immediately into the visible canvas. The viewer avoids native MJPEG image buffering, keeps one decode in flight, and replaces queued stale frames with the latest frame. Static pages no longer need another frame/heartbeat before a click becomes visible.
- Chromium compositor frames target up to 30 fps. A healthy static stream does not trigger a synchronous screenshot every two seconds; cached screenshot fallback remains available.
- Mouse press, move and release events are forwarded as native input. Hover, drag/selection, sliders, double-click and modifier clicks are supported. Pointer cancellation/window blur releases held buttons; canceled presses are released outside the viewport rather than replayed as clicks.
- Discrete mouse presses/releases flush immediately. Contiguous motion and typing are coalesced, and input/control delivery stays FIFO. Waiting for a field/draft action's processed result no longer holds the network delivery lane for subsequent mouse input.
- Focused fields report updates from the document receiving input. Normal clicks and typing no longer scan every iframe or wait for a full field read. Newly selected popup tabs are brought to the foreground.
- Sanitized input audit counts and per-batch timings are grouped and written off the Playwright input thread. Pending audit batches are flushed at controls/shutdown; typed text/passwords are not recorded in these counters.

The sidebar reports browser dispatch time, server queue time and the delay until a subsequent input-associated frame is drawn. The visible-frame measure indicates feedback delivery, not employer acceptance or successful form validation.

## Using the update

Hard-refresh the dashboard and start a new assisted-browser session. In **Take over**, click, drag, double-click, type and scroll directly in the live page. The page remains usable while an answer draft is running. Final review and submission approval remain separate controls.

## Measurements and regression checks

The same isolated form with ten visible iframes and a simulated 200 ms focus reader was tested before and after changes using real UI mouse clicks and pixel changes in the displayed live view:

- Before: mean **1,418 ms**, slowest of eight clicks **1,474 ms**.
- Updated focused run: mean **162 ms**, slowest of eight clicks **187 ms**—about **89% lower** mean click-to-visible latency in this fixture.

An authenticated lookup of a deliberately nonexistent session on the previous production container averaged **233 ms** per request, exposing workspace setup in the hot path. Live deployment checks compare that same lookup after the fix.

After deployment, that same signed-in lookup averaged **3.8 ms** over eight requests (first request 17.3 ms; subsequent requests 1.5–2.7 ms). The deployed Linux browser also passed native mouse, cancellation, compositor and unapproved-final-click guard checks on an isolated in-memory page.

Tests cover incremental/chunked frame parsing without waiting for a subsequent frame, bounded frame buffers, real dragging/double-clicking, mouse-selected text entry, cancellation, navigation, final submission guards, authentication/CSRF/ownership, and slow audit-writer isolation with complete final audit flushing. These are measured fixtures; employer-side page rendering and network responses remain part of actual navigation time.
