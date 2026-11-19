# Second Brain Builder

A self-hosted app that turns a person's documents into an LLM-maintained wiki. The wiki lives in a GitHub repository that the person and their agents can both use.

## Language

### Knowledge

**Brain**:
One bounded knowledge context, stored in exactly one GitHub repository. An agent that uses a Brain sees all of it and nothing outside it.
_Avoid_: vault, knowledge base

**Folder**:
A user-defined group of Sources inside one Brain, usually one subject, such as "Personal" or "Work". Folders can nest.
_Avoid_: collection, category, section

**Source**:
An original file that the user adds to a Brain, such as a PDF, an ebook, or a video. A Source never changes after it is added.
_Avoid_: raw file, upload, document

**Canonical Document**:
The faithful Markdown conversion of one Source. No LLM rewrites it.
_Avoid_: converted file, raw markdown, transcript

**Asset**:
A file extracted from a Source during Conversion, such as an image or a subtitle track, that a Canonical Document references.
_Avoid_: attachment, media

**Wiki Page**:
A page that the LLM writes from one or more Sources: a summary, entity, concept, comparison, or synthesis page. It cites the Sources it uses and belongs to the whole Brain, not to a Folder.
_Avoid_: note, article, concept (the wiki format uses "concept" for any typed page)

### Processing

**Conversion**:
The step that turns one Source into its Canonical Document and Assets. It needs no LLM.
_Avoid_: parsing, extraction, import

**Ingest**:
The step after Conversion in which the LLM reads a new Canonical Document and updates the Wiki Pages, the index, and the log.
_Avoid_: sync, processing, indexing

**Part**:
A section of a Canonical Document that Ingest sends to the LLM in one step. A long Source has many Parts, and its Ingest can stop and continue at the next Part.
_Avoid_: chunk (in UI text), page, segment

**Query**:
A question that an agent answers from one Brain, citing the Sources. A good answer can be saved as a synthesis Wiki Page.
_Avoid_: search, ask, chat

**Provider**:
An LLM endpoint that the user configures, OpenAI-compatible or Anthropic-compatible. It can run locally or in the cloud.
_Avoid_: model, API, backend
