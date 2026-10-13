"""Plain-English failure reasons with a next step, and quality warnings (SPEC §7.5)."""

from __future__ import annotations

from app.ideation.marker import IDEA_BRIEF_NEXT_STEP, IDEA_BRIEF_REASON


class IngestError(Exception):
    """A source could not be processed. ``reason`` and ``next_step`` are shown to the user."""

    def __init__(self, reason: str, next_step: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.next_step = next_step
        self.message = reason  # used by the job queue as the job's error text


def http_status_error(status: int) -> IngestError:
    """Failure for a web page that answered with an error status."""
    if status in (401, 402, 403, 407, 451):
        return IngestError(
            f"The site refused to share this page (HTTP {status}). It may block automated "
            "access, need a login, or be behind a paywall.",
            SAVE_AS_PDF,
        )
    if status == 404 or status == 410:
        return IngestError(
            f"The page was not found (HTTP {status}).", "Check the address and add it again."
        )
    if status == 429:
        return IngestError(
            "The site is limiting requests right now (HTTP 429).", "Wait a while, then Refresh."
        )
    if status >= 500:
        return IngestError(f"The site had an error (HTTP {status}).", "Try Refresh later.")
    return IngestError(f"The site answered with HTTP {status}.", SAVE_AS_PDF)


SAVE_AS_PDF = "Open the page in your browser, save it as PDF, and upload the file."
REFRESH = "Press Refresh to try again."

# --- failures (reason, next step) -----------------------------------------------------------

UNSUPPORTED_FILE = (
    "Trustbook can't read this type of file.",
    "Upload a PDF, Word, PowerPoint, Excel, text, Markdown, CSV, JSON, HTML or audio file.",
)
# Idea briefs are never sources (SPEC §10, D-099).
IDEA_BRIEF = (IDEA_BRIEF_REASON, IDEA_BRIEF_NEXT_STEP)
TRUSTBOOK_FOLDER = (
    "This is a Trustbook data folder. Its files are never used as sources.",
    "Link a folder with your own files instead.",
)
EMPTY_FILE = ("This file is empty.", "Check the file and upload it again.")
NO_TEXT = ("No text was found in this file.", "Check that the file contains text.")
SCANNED_PDF = (
    "This PDF has no text layer (it looks like a scanned image), and text recognition is not "
    "available in this build.",
    "Run OCR on the PDF, or upload a PDF with selectable text.",
)
SCANNED_PDF_NO_TEXT = (
    "No text could be read from this PDF, even with text recognition.",
    "Run OCR on the PDF, or upload a PDF with selectable text.",
)
OFFICE_NEEDS_PARSER = (
    "Reading Word, PowerPoint and Excel files needs the document parser, which is not available "
    "in this build.",
    "Save the file as PDF and upload that.",
)
DAMAGED_FILE = (
    "This file could not be read. It may be damaged or protected with a password.",
    "Open it, save a new copy without a password, and upload that.",
)
ENCRYPTED_PDF = (
    "This PDF is protected with a password.",
    "Remove the password and upload it again.",
)
PAGE_NO_TEXT = (
    "No readable text was found on this page. It may need a login, be behind a paywall, or "
    "show its content as images.",
    SAVE_AS_PDF,
)
PAGE_TOO_LARGE = ("This page is larger than 15 MB.", SAVE_AS_PDF)
UNREACHABLE = (
    "Trustbook could not reach this site.",
    "Check the address and your internet connection, then press Refresh.",
)
TIMEOUT = ("The site took too long to answer.", "Try Refresh later.")
TOO_MANY_REDIRECTS = ("The page redirected too many times.", SAVE_AS_PDF)
UNSUPPORTED_PAGE = (
    "This address points to a file type Trustbook can't read from a link.",
    "Download the file and upload it instead.",
)
NOT_YOUTUBE_VIDEO = (
    "This YouTube link does not point to a single video.",
    "Open the video and copy its address from the browser.",
)
YOUTUBE_UNAVAILABLE = (
    "YouTube did not give access to this video. It may be private, age-restricted, removed or "
    "blocked in your region.",
    "Check that the video plays in your browser, then press Refresh.",
)
NO_CAPTIONS_NO_STT = (
    "This video has no captions, and speech recognition is not available.",
    "Set up speech-to-text in Settings, or add a video that has captions.",
)
NO_STT = (
    "Speech recognition is not available, so this audio can't be transcribed.",
    "Set up speech-to-text in Settings, then add the file again.",
)
NO_SPEECH = ("No speech found in this audio.", "Check that the recording contains speech.")
AUDIO_UNREADABLE = (
    "This audio file could not be decoded.",
    "Convert it to MP3 or WAV and upload it again.",
)
AUDIO_TOO_LONG = (
    "This recording is longer than 4 hours.",
    "Split it into shorter parts and upload those.",
)
FOLDER_MISSING = (
    "The linked folder no longer exists or can't be read.",
    "Check that the folder is still in your home folder, then link it again.",
)
FOLDER_EMPTY = (
    "No readable files were found in this folder (after skipping ignored files, files over "
    "5 MB, binaries and files with possible secrets).",
    "Link a folder that contains documents or code.",
)
FOLDER_TOO_BIG = (
    "This folder has more than 20,000 files to read.",
    "Link a smaller folder, for example one project.",
)
SCANNER_MISSING = (
    "The secret scanner (gitleaks) is not installed, so the folder was not indexed. This keeps "
    "any passwords or keys in it from being indexed.",
    "Use the official Trustbook image, which includes gitleaks.",
)
SCAN_FAILED = ("The secret scan did not finish, so the folder was not indexed.", REFRESH)
NO_EMBEDDER = (
    "The embedding model is not available, so this source could not be indexed.",
    "Check the Embeddings card in Settings.",
)
EMBEDDING_CHANGED = (
    "This space was indexed with a different embedding model.",
    "Create a new space for new sources.",
)
REINDEX_NO_SNAPSHOT = (
    "The saved copy of this source could not be read, so it was not re-indexed.",
    "Refresh the source, or remove it and add it again.",
)
CANCELLED = ("Processing was cancelled.", "Remove this source, or add it again.")
CANCELLED_REFRESHABLE = ("Processing was cancelled.", REFRESH)
INTERNAL = ("Something went wrong while processing this source.", REFRESH)

# --- quality warnings -----------------------------------------------------------------------

WARN_WHISTLE = "Transcript made by speech recognition (Whistle); names and numbers may be wrong."
WARN_STT_ENDPOINT = "Transcript made by speech recognition; names and numbers may be wrong."
WARN_AUTO_CAPTIONS = "Transcript from YouTube's automatic captions; names may be wrong."
WARN_OCR = "Some text was read from images (OCR) and may contain errors."
WARN_SHORT = "Very little text was found (under 300 characters)."
WARN_BASIC_PDF = (
    "Read with the basic PDF reader because the document parser is not available; tables and "
    "layout may be lost."
)
SHORT_TEXT_CHARS = 300


def fail(pair: tuple[str, str]) -> IngestError:
    """Build an :class:`IngestError` from a (reason, next step) pair."""
    return IngestError(pair[0], pair[1])


def skipped_briefs_warning(count: int) -> str:
    noun = "file" if count == 1 else "files"
    return f"{count} {noun} skipped: Trustbook idea briefs are never used as sources."


def skipped_secrets_warning(count: int) -> str:
    noun = "file" if count == 1 else "files"
    return f"{count} {noun} skipped: possible secrets."
