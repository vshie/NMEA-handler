# USB port mapping: NMEA-handler vs BlueOS PR 3403

## Summary

| Aspect | NMEA-handler (ours) | BlueOS PR 3403 |
|--------|---------------------|----------------|
| **Input** | `/dev/serial/by-path` symlink **name** (e.g. `platform-...-usb-0:1.1.3:1.0-port0`) | Full **path** string (e.g. `/dev/serial/by-path/...`) |
| **Matching** | Regex extract `bus_path` (e.g. `0:1.1.3`), then **segment checks** (`:1.1.3`, `:1.2`, etc.) | **Prefix match**: strip `-port0` suffix, then `usbRoot.includes(key)` over a static map |
| **Pi4 positions** | 1.1.2→bottom-left, 1.1.3→top-left, 1.2→bottom-right, 1.3→top-right (USB 2.0 vs 3.0 labeled) | 1.3→top-left, 1.4→bottom-left, 1.1→top-right, 1.2→bottom-right (position only) |
| **Boards** | Pi4 only | **Pi3, Pi4, Pi5** (different platform prefixes) |
| **Hub handling** | Generic fallback for `1.1.x` (USB 2.0) | **Explicit**: regex for `usb-0:(?:[0-9]+\.)+([0-9]+):1.0` → overlay "Connected via hub, device is on hub port X" |
| **Path key format** | N/A (we don’t store full path) | Keys are **prefixes** without `:1.0-port0` (e.g. `...usb-0:1.3`) so one map works for different interface suffixes |
| **Extra metadata** | None | **Udev**: DEVNAME, ID_MODEL, ID_USB_DRIVER, ID_SERIAL from backend for table and condensed label |

## BlueOS strengths we could adopt

1. **Prefix-based matching**  
   They match on the USB “root” (path with `-port0` and interface part stripped), then check if the map key is **contained** in that string. That avoids depending on the exact `:1.0-port0` or `:1.0` suffix and works with different interface numbers.

2. **Multiple boards**  
   One map per board (Pi3, Pi4, Pi5) with different platform prefixes. We only handle Pi4.

3. **Hub overlay**  
   When the path looks like a hub (e.g. `...1.4.3:1.0...`), they show “Connected via hub, device is on hub port X” so users know it’s not a direct port. We only have a generic “USB 2.0” fallback for `1.1.x`.

4. **Richer display**  
   They use udev (device name, model, driver, serial) for a dense table and a condensed one-line label; we only show by-id and our position label.

## Our strengths

1. **USB 2.0 vs 3.0**  
   We label “USB 2.0 Top/Bottom” vs “USB 3.0 Top/Bottom” using the kernel’s 1.1.x (USB2) vs 1.2/1.3 (USB3). BlueOS only shows position (top-left, etc.).

2. **Same path format as we see**  
   On the same BlueOS/Pi4 setup, our logs show `1.1.3`, `1.1.4`, `1.2`, `1.3`; we map those explicitly. BlueOS’s Pi4 map uses 1.1, 1.2, 1.3, 1.4 — so either their paths differ (e.g. no 1.1.x) or they use a different numbering convention for the four physical ports.

## Recommendation

- **Keep** our Pi4 segment-based mapping (1.1.2, 1.1.3, 1.2, 1.3) and USB 2/3 labels; they match the paths we actually see.
- **Add** prefix-style fallback: normalize the path (e.g. strip `-port0` and `:1.0` / `:1.0-port0`) and try a small **prefix map** (e.g. Pi4 `...usb-0:1.1`, `...usb-0:1.2`, ...) so we still get a position when the path format varies.
- **Add** Pi3 and Pi5 maps (same position semantics as BlueOS) so we support all common BlueOS boards.
- **Add** hub detection: if the path matches a hub pattern (e.g. more than one dot in the port segment), set a “via hub, port X” flag and show it in the UI so behaviour matches BlueOS’s “figured out how to map … quite well” experience.
