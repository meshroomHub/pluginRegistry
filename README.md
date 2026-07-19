# ![Meshroom - Plugin Registry](/docs/banner-meshroom-registry.png)

Hosts and maintains an up-to-date plugin registry file, allowing Meshroom users to easily discover and fetch MeshroomHub plugins.

## plugins.json

[plugins.json](plugins.json) lists every [meshroomHub](https://github.com/meshroomHub) public repository that is a Meshroom plugin, along with the version Meshroom should fetch:

```json
{
    "url": "https://github.com/meshroomHub/mrSegmentation",
    "version": "1.4.1"
}
```

or, for a repository with no tags:

```json
{
    "url": "https://github.com/meshroomHub/mrHelloWorld",
    "version": "main+bbc9af0"
}
```

- `url` — the repository's GitHub URL.
- `version` — the repository's latest tag name, or `<default-branch>+<short-sha>` of its latest commit if it has no tags.

## Automatic updates

[.github/workflows/update-plugins.yml](.github/workflows/update-plugins.yml) runs daily and regenerates `plugins.json` via [scripts/update_plugins.py](scripts/update_plugins.py):

1. List every public repository in the `meshroomHub` org.
2. Keep those with a root `meshroom` folder, these are the Meshroom plugins.
3. For each plugin, compute its current version: its newest tag (by commit date) if it has tags, otherwise `<default-branch>+<short-sha>` of its latest commit.
4. Rewrite `plugins.json` with these current values.
5. If that changed the file, commit it and tag the commit `<year>.<month>.<day>`.

