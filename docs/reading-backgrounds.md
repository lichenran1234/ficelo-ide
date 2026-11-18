# Reading and interface backgrounds

Settings has separate background controls for the interface, rendered Markdown, and code editors. Use a slider or enter a gray hex color such as `#888` or `#F2F2F2`. The controls apply in Light mode; Dark mode and Review diffs keep their own appearance.

The code background also applies to Markdown's source editor. Text, syntax colors, and Markdown table shades adjust for contrast.

Changes are saved automatically. **Reset to defaults** resets appearance settings, not just the background colors. Exact defaults and accepted ranges live in [settings.js](../src/ficelo/web/js/settings.js).

## Per-file overrides

For an individual file, merge a `ficelo.filebackgrounds` map into the existing `settings.json` in your [configuration directory](configuration.md):

```json
{
  "ficelo.filebackgrounds": {
    "/documents/reading.md": "#efefef",
    "/projects/example.py": "#e8e8e8"
  }
}
```

Keys are absolute filesystem paths, even with a narrower served root. Values are six-digit hex colors. An override takes precedence over the reading sliders in Light mode; remove it to follow the sliders again. Reload the browser after editing the map.
