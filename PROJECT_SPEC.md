# Project contract

A project is a directory under `projects/<id>`.

## Required directories

- `source/`
- `assets/images/`
- `assets/video/`
- `assets/audio/`
- `assets/fonts/`
- `assets/models3d/`
- `assets/data/`
- `narration/`
- `renders/preview/`
- `renders/final/`

## Entry point

`source/index.html`

The renderer opens this file with a `file://` URL, so local relative paths are preferred.

## Storyboard

`storyboard.json`:

```json
{
  "scenes": [
    {
      "id": "scene-1",
      "duration_seconds": 8,
      "title": "..."
    }
  ]
}
```

The renderer uses the sum of `duration_seconds` as the capture duration.

## Asset discipline

The agent does not choose arbitrary asset directories through semantic asset tools. The tool chooses the directory from the declared asset type.

Binary asset ingestion is deliberately a separate extension point: a production implementation should add authenticated upload/download tools that verify MIME type and extension before placing files.
