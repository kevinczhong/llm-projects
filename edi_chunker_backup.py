"""
EDI Specification Markdown Chunker

Optimized for embedding technical documentation like EDI mapping specs.
Preserves semantic coherence by:
- Respecting markdown section boundaries
- Keeping tables and code blocks intact
- Including parent header context in each chunk
- Handling hierarchical document structure
"""

import re
from dataclasses import dataclass, field
from typing import Iterator
from dotenv import load_dotenv
from pinecone import Pinecone
import os

load_dotenv('.env')

pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))
dense_index = pc.Index("edi-spec") # replace 'maven-gross' with your own index name

@dataclass
class Chunk:
    """A semantic chunk of the document."""
    content: str
    metadata: dict = field(default_factory=dict)

    @property
    def token_estimate(self) -> int:
        """Rough token estimate (words * 1.3)."""
        return int(len(self.content.split()) * 1.3)


class EDISpecChunker:
    """
    Chunker optimized for EDI specification markdown documents.

    Design decisions:
    - Chunks by section headers (## and ###) as primary boundaries
    - Preserves tables completely (critical for mapping specs)
    - Preserves code blocks completely (EDI/JSON examples)
    - Adds hierarchical header context to each chunk
    - Merges small adjacent sections to avoid tiny chunks
    - Splits oversized sections while preserving atomic elements
    """

    def __init__(
        self,
        max_chunk_size: int = 1500,  # chars, roughly 300-400 tokens
        min_chunk_size: int = 200,   # merge smaller chunks
        overlap_size: int = 100,     # chars of overlap for context
    ):
        self.max_chunk_size = max_chunk_size
        self.min_chunk_size = min_chunk_size
        self.overlap_size = overlap_size

        # Patterns for markdown elements
        self.header_pattern = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)
        self.table_pattern = re.compile(
            r'(\|.+\|\n\|[-:\s|]+\|\n(?:\|.+\|\n)*)',
            re.MULTILINE
        )
        self.code_block_pattern = re.compile(
            r'(```\w*\n[\s\S]*?```)',
            re.MULTILINE
        )

    def chunk(self, text: str) -> list[Chunk]:
        """
        Split markdown text into semantic chunks.

        Returns list of Chunk objects with content and metadata.
        """
        # Parse document structure
        sections = self._parse_sections(text)

        # Process sections into chunks
        chunks = []
        for section in sections:
            section_chunks = self._process_section(section)
            chunks.extend(section_chunks)

        # Merge small chunks
        chunks = self._merge_small_chunks(chunks)

        # Add overlap for retrieval
        chunks = self._add_overlap(chunks)

        return chunks

    def _parse_sections(self, text: str) -> list[dict]:
        """
        Parse markdown into hierarchical sections.

        Each section includes:
        - level: header depth (1-6)
        - title: header text
        - content: section body
        - path: list of parent headers for context
        """
        sections = []
        header_stack = []  # Track hierarchy

        # Split by headers while keeping the header with content
        parts = re.split(r'(?=^#{1,6}\s)', text, flags=re.MULTILINE)

        for part in parts:
            if not part.strip():
                continue

            # Check if this part starts with a header
            header_match = self.header_pattern.match(part)

            if header_match:
                level = len(header_match.group(1))
                title = header_match.group(2).strip()
                content = part[header_match.end():].strip()

                # Update header stack for hierarchy tracking
                while header_stack and header_stack[-1]['level'] >= level:
                    header_stack.pop()

                path = [h['title'] for h in header_stack]

                section = {
                    'level': level,
                    'title': title,
                    'content': content,
                    'path': path.copy(),
                    'full_header': header_match.group(0)
                }

                sections.append(section)
                header_stack.append({'level': level, 'title': title})
            else:
                # Content before first header (preamble)
                if part.strip():
                    sections.append({
                        'level': 0,
                        'title': 'Document Start',
                        'content': part.strip(),
                        'path': [],
                        'full_header': ''
                    })

        return sections

    def _process_section(self, section: dict) -> list[Chunk]:
        """
        Process a section into one or more chunks.

        Preserves:
        - Complete tables
        - Complete code blocks
        - Logical paragraph groupings
        """
        content = section['content']
        header = section['full_header']
        path = section['path']
        title = section['title']

        # Build context prefix from hierarchy
        context = self._build_context(path, title)

        # If section is small enough, return as single chunk
        full_content = f"{header}\n\n{content}".strip()
        if len(full_content) <= self.max_chunk_size:
            return [Chunk(
                content=full_content,
                metadata={
                    'section': title,
                    'path': path,
                    'level': section['level'],
                    'type': 'section'
                }
            )]

        # Section is too large - split carefully
        return self._split_large_section(section, context)

    def _split_large_section(self, section: dict, context: str) -> list[Chunk]:
        """
        Split a large section while preserving atomic elements.

        Priority order for keeping together:
        1. Code blocks
        2. Tables
        3. Paragraphs
        """
        content = section['content']
        chunks = []

        # Identify atomic blocks that shouldn't be split
        atomic_blocks = []

        # Find code blocks
        for match in self.code_block_pattern.finditer(content):
            atomic_blocks.append({
                'start': match.start(),
                'end': match.end(),
                'content': match.group(0),
                'type': 'code'
            })

        # Find tables
        for match in self.table_pattern.finditer(content):
            atomic_blocks.append({
                'start': match.start(),
                'end': match.end(),
                'content': match.group(0),
                'type': 'table'
            })

        # Sort by position
        atomic_blocks.sort(key=lambda x: x['start'])

        # Split content around atomic blocks
        segments = []
        pos = 0
        for block in atomic_blocks:
            if block['start'] > pos:
                # Text before this block
                text = content[pos:block['start']].strip()
                if text:
                    segments.append({'content': text, 'type': 'text'})
            segments.append(block)
            pos = block['end']

        # Remaining text after last block
        if pos < len(content):
            text = content[pos:].strip()
            if text:
                segments.append({'content': text, 'type': 'text'})

        # Build chunks from segments using list for O(n) concatenation
        header_prefix = f"{section['full_header']}\n\n"
        current_parts = [header_prefix]
        current_length = len(header_prefix)

        for segment in segments:
            segment_content = segment['content']
            segment_with_spacing = segment_content + "\n\n"
            segment_len = len(segment_with_spacing)

            # Check if adding this segment exceeds max size
            if current_length + segment_len > self.max_chunk_size:
                # Save current chunk if it has content beyond just the header
                if current_length > len(header_prefix):
                    chunks.append(Chunk(
                        content=''.join(current_parts).strip(),
                        metadata={
                            'section': section['title'],
                            'path': section['path'],
                            'level': section['level'],
                            'type': 'section_part'
                        }
                    ))

                # Start new chunk with context
                context_prefix = f"{context}\n\n"
                current_parts = [context_prefix]
                current_length = len(context_prefix)

            # Add segment to current chunk
            current_parts.append(segment_with_spacing)
            current_length += segment_len

        # Don't forget the last chunk
        if current_length > 0 and any(p.strip() for p in current_parts):
            chunks.append(Chunk(
                content=''.join(current_parts).strip(),
                metadata={
                    'section': section['title'],
                    'path': section['path'],
                    'level': section['level'],
                    'type': 'section_part'
                }
            ))

        return chunks

    def _build_context(self, path: list[str], title: str) -> str:
        """Build context string from header hierarchy."""
        if not path:
            return f"## {title}" if title else ""

        # Include immediate parent for context
        context_parts = []
        for i, p in enumerate(path):
            prefix = '#' * (i + 1)
            context_parts.append(f"{prefix} {p}")

        return "\n".join(context_parts[-2:])  # Last 2 parents max

    def _merge_small_chunks(self, chunks: list[Chunk]) -> list[Chunk]:
        """Merge adjacent small chunks to avoid fragmentation."""
        if not chunks:
            return chunks

        merged = []
        current = chunks[0]

        for next_chunk in chunks[1:]:
            combined_size = len(current.content) + len(next_chunk.content)
            same_parent = (
                current.metadata.get('path') == next_chunk.metadata.get('path') or
                current.metadata.get('section') in next_chunk.metadata.get('path', [])
            )

            if (len(current.content) < self.min_chunk_size and
                combined_size <= self.max_chunk_size and
                same_parent):
                # Merge chunks
                current = Chunk(
                    content=f"{current.content}\n\n{next_chunk.content}",
                    metadata={
                        **current.metadata,
                        'merged': True,
                        'sections': [
                            current.metadata.get('section', ''),
                            next_chunk.metadata.get('section', '')
                        ]
                    }
                )
            else:
                merged.append(current)
                current = next_chunk

        merged.append(current)
        return merged

    def _add_overlap(self, chunks: list[Chunk]) -> list[Chunk]:
        """Add overlap between chunks for better retrieval context."""
        if len(chunks) <= 1 or self.overlap_size <= 0:
            return chunks

        result = []
        for i, chunk in enumerate(chunks):
            content = chunk.content

            # Add end of previous chunk as prefix context
            if i > 0:
                prev_content = chunks[i - 1].content
                overlap = prev_content[-self.overlap_size:].strip()
                if overlap and not content.startswith(overlap):
                    # Only add if it doesn't duplicate
                    content = f"[...] {overlap}\n\n---\n\n{content}"

            result.append(Chunk(
                content=content,
                metadata={
                    **chunk.metadata,
                    'chunk_index': i,
                    'total_chunks': len(chunks)
                }
            ))

        return result

    def chunk_for_embedding(self, text: str) -> Iterator[dict]:
        """
        Convenience method returning dicts ready for embedding APIs.

        Yields dicts with 'text' and 'metadata' keys.
        """
        for i, chunk in enumerate(self.chunk(text)):
            yield {
                'id': f'edi_spec-chunk-{i}',
                'text': chunk.content,
                'metadata': chunk.metadata.get('section', ''),
                # 'token_estimate': chunk.token_estimate
            }


def main():
    """Demo: chunk the EDI spec and show results."""
    import json
    from pathlib import Path

    # Read the EDI spec
    spec_path = Path(__file__).parent / "data" / "edi_spec.md"
    if not spec_path.exists():
        print(f"File not found: {spec_path}")
        return

    text = spec_path.read_text()

    # Create chunker with settings optimized for embedding
    chunker = EDISpecChunker(
        max_chunk_size=1500,  # ~300-400 tokens, good for most embedding models
        min_chunk_size=200,
        overlap_size=100
    )

    # Chunk the document
    chunks = list(chunker.chunk_for_embedding(text))

    # Print summary
    print(f"Document chunked into {len(chunks)} chunks\n")
    print("-" * 60)

    # for i, chunk in enumerate(chunks):
    #     print(f"\n### Chunk {i + 1} ###")
    #     # print(f"Section: {chunk['metadata'].get('section', 'N/A')}")
    #     print(f"Path: {' > '.join(chunk['metadata'].get('path', []))}")
    #     # print(f"Tokens (est): {chunk['token_estimate']}")
    #     print(f"Characters: {len(chunk['text'])}")
    #     print("-" * 40)
    #     # Show first 200 chars
    #     preview = chunk['text'][:200].replace('\n', ' ')
    #     print(f"Preview: {preview}...")
    #     print()

    # Save chunks to JSON for inspection
    output_path = Path(__file__).parent / "data" / "edi_spec_chunks.json"
    with open(output_path, 'w') as f:
        json.dump(chunks, f, indent=2)
    print(f"\nChunks saved to: {output_path}")

    # Upsert to Pinecone
    dense_index.upsert_records("850_PO_implementation", chunks)


if __name__ == "__main__":
    main()
