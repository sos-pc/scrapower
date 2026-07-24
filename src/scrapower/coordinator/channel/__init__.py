"""Channel transcription subsystem.

Isolated, optional application layer on top of the generic Scrapower engine:
discovers a YouTube channel's playlists/videos, submits one whisper task per
unique video (the coordinator + harvester + workers do the parallel work),
then renders each transcript to Markdown and delivers it to Google Drive
(and/or a local staging dir), organised by playlist.

Nothing here touches the generic task-dispatch hot path.
"""
