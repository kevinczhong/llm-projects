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
    - Streaming architecture for memory efficiency
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
        Returns list of Chunk objects (consumes generator).
        """
        return list(self.chunk_stream(text))

    def chunk_stream(self, text: str) -> Iterator[Chunk]:
        """
        Stream chunks from markdown text.
        Memory efficient for large documents.
        """
        # 1. Stream sections
        sections = self._iter_sections(text)
        
        # 2. Process sections into raw chunks (flat stream)
        raw_chunks = self._process_sections_stream(sections)
        
        # 3. Merge small chunks
        merged_chunks = self._merge_small_chunks_stream(raw_chunks)
        
        # 4. Add overlap
        final_chunks = self._add_overlap_stream(merged_chunks)
        
        yield from final_chunks

    def _iter_sections(self, text: str) -> Iterator[dict]:
        """
        Yield sections lazily using regex finditer.
        This avoids loading a massive list of splits into memory.
        """
        header_stack = []
        
        # Re-implementation of _iter_sections with a simpler cursor logic
        # Find all headers first? No, that stores them all.
        # Iterate headers. The content of header N is [header_N_end : header_N+1_start]
        
        matches = list(self.header_pattern.finditer(text))
        
        # Handle preamble
        if matches and matches[0].start() > 0:
             yield {
                'level': 0,
                'title': 'Document Start',
                'content': text[:matches[0].start()].strip(),
                'path': [],
                'full_header': ''
            }
        elif not matches and text.strip():
             # No headers at all
             yield {
                'level': 0,
                'title': 'Document Start',
                'content': text.strip(),
                'path': [],
                'full_header': ''
            }
            
        for i, match in enumerate(matches):
            level = len(match.group(1))
            title = match.group(2).strip()
            header_full = match.group(0)
            
            start = match.end()
            end = matches[i+1].start() if i + 1 < len(matches) else len(text)
            content = text[start:end].strip()
            
            # Update stack
            while header_stack and header_stack[-1]['level'] >= level:
                header_stack.pop()
            
            path = [h['title'] for h in header_stack]
            
            yield {
                'level': level,
                'title': title,
                'content': content,
                'path': path.copy(),
                'full_header': header_full
            }
            
            header_stack.append({'level': level, 'title': title})


    def _process_sections_stream(self, sections: Iterator[dict]) -> Iterator[Chunk]:
        """Convert sections to chunks."""
        for section in sections:
            yield from self._process_section(section)

    def _process_section(self, section: dict) -> list[Chunk]:
        """Process a single section into one or more chunks."""
        content = section['content']
        header = section['full_header']
        path = section['path']
        title = section['title']

        context = self._build_context(path, title)
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

        return self._split_large_section(section, context)

    def _split_large_section(self, section: dict, context: str) -> list[Chunk]:
        """Split a large section preserving atomic elements."""
        content = section['content']
        chunks = []
        
        # Identify atomic blocks
        atomic_blocks = []
        for match in self.code_block_pattern.finditer(content):
            atomic_blocks.append({'start': match.start(), 'end': match.end(), 'content': match.group(0), 'type': 'code'})
        for match in self.table_pattern.finditer(content):
            atomic_blocks.append({'start': match.start(), 'end': match.end(), 'content': match.group(0), 'type': 'table'})
        
        atomic_blocks.sort(key=lambda x: x['start'])
        
        segments = []
        pos = 0
        for block in atomic_blocks:
            if block['start'] > pos:
                text = content[pos:block['start']].strip()
                if text:
                    segments.extend(self._split_text_segment(text))
            segments.append(block)
            pos = block['end']
            
        if pos < len(content):
            text = content[pos:].strip()
            if text:
                segments.extend(self._split_text_segment(text))

        # Build chunks
        header_prefix = f"{section['full_header']}\n\n"
        current_parts = [header_prefix]
        current_length = len(header_prefix)

        for segment in segments:
            segment_content = segment['content']
            segment_with_spacing = segment_content + "\n\n"
            segment_len = len(segment_with_spacing)

            if current_length + segment_len > self.max_chunk_size:
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
                
                context_prefix = f"{context}\n\n"
                current_parts = [context_prefix]
                current_length = len(context_prefix)
                
                # Edge case: Single segment larger than max_chunk_size even with fresh chunk
                # In this case we just have to allow it (it's atomic) or the text splitter failed.
                # Since we split text segments, this only happens for massive code blocks/tables.

            current_parts.append(segment_with_spacing)
            current_length += segment_len

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

    def _split_text_segment(self, text: str) -> list[dict]:
        """
        Split a plain text segment if it's too large.
        Returns list of dicts with 'content' and 'type'.
        """
        # Heuristic: If text is small enough, return as is
        if len(text) < self.max_chunk_size:
            return [{'content': text, 'type': 'text'}]
            
        # Split by double newlines (paragraphs)
        paragraphs = text.split('\n\n')
        segments = []
        buffer = []
        buffer_len = 0
        
        for para in paragraphs:
            para = para.strip()
            if not para: continue
            
            # If a single paragraph is massive, we might need to split by lines or sentences.
            # For now, let's treat it as an atomic unit to avoid breaking sentences,
            # unless it's truly huge (2x max size), then split by lines.
            
            if len(para) > self.max_chunk_size * 1.5:
                # Emergency line split
                lines = para.split('\n')
                for line in lines:
                    segments.append({'content': line, 'type': 'text_line'})
                continue

            segments.append({'content': para, 'type': 'text_para'})
            
        return segments

    def _merge_small_chunks_stream(self, chunks: Iterator[Chunk]) -> Iterator[Chunk]:
        """Merge small chunks in a stream."""
        buffer = None

        for chunk in chunks:
            if buffer is None:
                buffer = chunk
                continue

            combined_size = len(buffer.content) + len(chunk.content)
            same_parent = (
                buffer.metadata.get('path') == chunk.metadata.get('path') or
                buffer.metadata.get('section') in chunk.metadata.get('path', [])
            )

            if (len(buffer.content) < self.min_chunk_size and
                combined_size <= self.max_chunk_size and
                same_parent):
                # Merge
                buffer = Chunk(
                    content=f"{buffer.content}\n\n{chunk.content}",
                    metadata={
                        **buffer.metadata,
                        'merged': True,
                        'sections': [
                            buffer.metadata.get('section', ''),
                            chunk.metadata.get('section', '')
                        ]
                    }
                )
            else:
                yield buffer
                buffer = chunk
        
        if buffer:
            yield buffer

    def _add_overlap_stream(self, chunks: Iterator[Chunk]) -> Iterator[Chunk]:
        """Add overlap to chunks stream."""
        prev_content = ""
        index = 0
        
        for chunk in chunks:
            content = chunk.content
            
            if prev_content and self.overlap_size > 0:
                overlap = prev_content[-self.overlap_size:].strip()
                if overlap and not content.startswith(overlap):
                    content = f"[...] {overlap}\n\n---\n\n{content}"
            
            yield Chunk(
                content=content,
                metadata={
                    **chunk.metadata,
                    'chunk_index': index
                }
            )
            
            prev_content = chunk.content # store original content for next overlap
            index += 1

    def _build_context(self, path: list[str], title: str) -> str:
        """Build context string from header hierarchy."""
        if not path:
            return f"## {title}" if title else ""

        context_parts = []
        for i, p in enumerate(path):
            prefix = '#' * (i + 1)
            context_parts.append(f"{prefix} {p}")

        return "\n".join(context_parts[-2:])  # Last 2 parents max

    def chunk_for_embedding(self, text: str) -> Iterator[dict]:
        """Convenience method returning dicts ready for embedding APIs."""
        for i, chunk in enumerate(self.chunk_stream(text)):
            yield {
                'id': i,
                'text': chunk.content,
                'metadata': chunk.metadata,
                'token_estimate': chunk.token_estimate
            }


def main():
    """Demo: chunk the EDI spec and show results."""
    import json
    from pathlib import Path

    spec_path = Path(__file__).parent / "data" / "edi_spec.md"
    if not spec_path.exists():
        print(f"File not found: {spec_path}")
        return

    text = spec_path.read_text()

    chunker = EDISpecChunker(
        max_chunk_size=1500,
        min_chunk_size=200,
        overlap_size=100
    )

    chunks = list(chunker.chunk_for_embedding(text))

    print(f"Document chunked into {len(chunks)} chunks\n")
    print("-" * 60)

    for i, chunk in enumerate(chunks[:5]): # Show first 5 only for brevity
        print(f"\n### Chunk {i + 1} ###")
        print(f"Section: {chunk['metadata'].get('section', 'N/A')}")
        print(f"Path: {' > '.join(chunk['metadata'].get('path', []))}")
        print(f"Tokens (est): {chunk['token_estimate']}")
        print(f"Characters: {len(chunk['text'])}")
        print("-" * 40)
        preview = chunk['text'][:200].replace('\n', ' ')
        print(f"Preview: {preview}...")
        print()

    output_path = Path(__file__).parent / "data" / "edi_spec_chunks.json"
    with open(output_path, 'w') as f:
        json.dump(chunks, f, indent=2)
    print(f"\nChunks saved to: {output_path}")


if __name__ == "__main__":
    main()
