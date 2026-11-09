"""Split timestamped segments into blocks suitable for incremental summaries."""
import json
from pathlib import Path


def format_timestamp(seconds):
    total = max(0, int(round(float(seconds))))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return '{:02d}:{:02d}:{:02d}'.format(hours, minutes, seconds)


def split_records(records, block_seconds):
    if block_seconds <= 0:
        return [records] if records else []
    blocks = []
    current = []
    for record in records:
        if current and record['end'] - current[0]['start'] >= block_seconds:
            blocks.append(current)
            current = []
        current.append(record)
    if current:
        blocks.append(current)
    return blocks


def write_blocks(records, output_dir, block_seconds):
    """Write blocks/<n>.jsonl and .txt; return metadata with absolute paths."""
    blocks_dir = Path(output_dir) / 'blocks'
    if block_seconds <= 0:
        if blocks_dir.exists():
            for stale in list(blocks_dir.glob('block-[0-9][0-9][0-9][0-9].txt')) + list(blocks_dir.glob('block-[0-9][0-9][0-9][0-9].jsonl')):
                stale.unlink()
        return []
    blocks_dir.mkdir(parents=True, exist_ok=True)
    for stale in list(blocks_dir.glob('block-[0-9][0-9][0-9][0-9].txt')) + list(blocks_dir.glob('block-[0-9][0-9][0-9][0-9].jsonl')):
        stale.unlink()
    result = []
    for index, records_in_block in enumerate(split_records(records, block_seconds), start=1):
        stem = 'block-{:04d}'.format(index)
        segments_path = blocks_dir / (stem + '.jsonl')
        text_path = blocks_dir / (stem + '.txt')
        temporary = segments_path.with_name(segments_path.name + '.part')
        temporary.write_text(''.join(json.dumps(record, ensure_ascii=False) + '\n'
                                     for record in records_in_block), encoding='utf-8')
        temporary.replace(segments_path)
        temporary = text_path.with_name(text_path.name + '.part')
        temporary.write_text(''.join('[{}] {}\n'.format(format_timestamp(record['start']),
                                                        record['text'].strip())
                                     for record in records_in_block), encoding='utf-8')
        temporary.replace(text_path)
        result.append({
            'index': index,
            'start': records_in_block[0]['start'],
            'end': records_in_block[-1]['end'],
            'segment_count': len(records_in_block),
            'characters': sum(len(record['text'].strip()) for record in records_in_block),
            'text': str(text_path.resolve()),
            'segments': str(segments_path.resolve()),
        })
    return result
