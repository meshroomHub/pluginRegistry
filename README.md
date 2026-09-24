# ![Meshroom - Plugin Registry](/docs/banner-meshroom-registry.png)

Hosts and maintains an up-to-date plugin registry file, allowing Meshroom users to easily discover and fetch MeshroomHub plugins.

## meshroomHub.json

[meshroomHub.json](meshroomHub.json) lists every [meshroomHub](https://github.com/meshroomHub) public repository that is a Meshroom plugin, along with the versions Meshroom can fetch:

```json
{
    "name": "Meshroom Hub",
    "description": "Meshroom Hub plugin registry",
    "url": "https://github.com/meshroomHub/pluginRegistry",
    "fileUrl": "https://raw.githubusercontent.com/meshroomHub/pluginRegistry/HEAD/meshroomHub.json",
    "entries": [
        {
            "url": "https://github.com/meshroomHub/mrSegmentation",
            "versions": ["1.4.1", "1.4.0", "1.3.0"],
            "sizeMB": 12
        },
        {
            "url": "https://github.com/meshroomHub/mrHelloWorld",
            "versions": ["main+bbc9af0"],
            "sizeMB": 1
        }
    ]
}
```

- `name` — the registry's name.
- `description` — the registry's description.
- `url` — the URL of this registry project.
- `fileUrl` — the URL Meshroom should fetch to get this file.
- `entries` — the list of plugins:
  - `url` — the plugin repository's GitHub URL.
  - `versions` — the plugin's tag names, newest first (up to the 5 most recent), or a single `<default-branch>+<short-sha>` entry if the repository has no tags.
  - `sizeMB` (optional) — the plugin approximated size in MB (rounded up).

## Automatic updates

[.github/workflows/update-plugins.yml](.github/workflows/update-plugins.yml) runs daily and regenerates `meshroomHub.json` via [scripts/update_plugins.py](scripts/update_plugins.py):

1. List every public repository in the `meshroomHub` org.
2. Keep those with a root `meshroom` folder, these are the Meshroom plugins.
3. For each plugin, compute its `versions` (its up to 5 newest tags, by commit date, or otherwise `<default-branch>+<short-sha>` of its latest commit) and `sizeMB` (its approximate size).
4. Rewrite `meshroomHub.json` with these current values.
5. If that changed the file, commit it and tag the commit `<year>.<month>.<day>`.

