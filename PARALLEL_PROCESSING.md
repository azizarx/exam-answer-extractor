# Multi-Threading Optimization Guide

## Overview

The Exam Answer Extractor now supports **multi-threaded parallel processing** for dramatically faster PDF extraction. Instead of processing pages sequentially, the system can process multiple pages simultaneously using Python's `ThreadPoolExecutor`.

## Performance Benefits

For a typical multi-page exam PDF:
- **Sequential Processing**: ~5-8 seconds per page
- **Parallel Processing (4 workers)**: ~1.5-2.5 seconds per page (effective)
- **Speedup**: **2-3x faster** for documents with 4+ pages

Example: A 10-page exam that takes 60 seconds sequentially can be processed in ~20 seconds with parallel processing.

## Configuration

Multi-threading is controlled via environment variables or `backend/config.py`:

```python
# Enable/disable parallel extraction
use_parallel_extraction: bool = True

# Number of concurrent workers
max_extraction_workers: int = 4
```

### Environment Variables

Add to your `.env` file:

```bash
# Enable parallel extraction (default: true)
USE_PARALLEL_EXTRACTION=true

# Number of parallel workers (default: 4)
# Recommended: 2-6 depending on your API rate limits
MAX_EXTRACTION_WORKERS=4
```

## How It Works

1. **Page Distribution**: When a PDF is converted to images, each page is assigned to a worker thread
2. **Parallel Execution**: Multiple pages are sent to the Gemini API simultaneously
3. **Result Aggregation**: Results are collected as they complete and merged into a single response
4. **Progress Tracking**: Real-time progress updates are logged for each page

## API Usage

### Automatic (Default Behavior)

Both upload endpoints automatically use parallel processing when enabled:

```bash
# Standard upload endpoint (with database tracking)
POST /upload

# Synchronous extraction endpoint
POST /extract/json
```

### Programmatic Control

You can also control parallelization programmatically:

```python
from backend.services.ai_extractor import get_ai_extractor

ai_extractor = get_ai_extractor()

# Enable parallel processing with custom worker count
result = ai_extractor.extract_from_multiple_images(
    image_paths=["page1.jpg", "page2.jpg", "page3.jpg"],
    use_parallel=True,
    max_workers=6
)

# Force sequential processing
result = ai_extractor.extract_from_multiple_images(
    image_paths=["page1.jpg", "page2.jpg"],
    use_parallel=False
)
```

## Performance Testing

Run the included performance benchmark:

```bash
python test_parallel_performance.py
```

This script will:
1. Convert a test PDF to images
2. Run extraction sequentially
3. Run extraction in parallel
4. Compare performance metrics

Example output:
```
======================================================================
PERFORMANCE SUMMARY
======================================================================
Sequential time:  45.32s
Parallel time:    18.67s
Time saved:       26.65s
Speedup:          2.43x faster
Improvement:      58.8% faster
======================================================================
```

## Best Practices

### Worker Count Optimization

- **Small PDFs (1-3 pages)**: Use `max_workers=2` or disable parallel processing
- **Medium PDFs (4-10 pages)**: Use `max_workers=4` (default)
- **Large PDFs (10+ pages)**: Use `max_workers=6-8`
- **API Rate Limits**: Stay within your Gemini API quota (adjust workers accordingly)

### When to Use Sequential Processing

Disable parallel processing (`use_parallel=False`) when:
- Processing single-page documents
- Working with strict API rate limits
- Debugging extraction issues
- Running on systems with limited resources

## Rate Limits & Costs

⚠️ **Important**: Parallel processing makes multiple API calls simultaneously. Ensure your Google Gemini API plan supports the request rate.

- **Free Tier**: 60 requests/minute → Use `max_workers=2-3`
- **Standard Tier**: Higher limits → Use `max_workers=4-8`
- **Costs**: Same per-page cost, just processed faster

## Monitoring

Performance metrics are logged automatically:

```python
# Check logs for timing information
logger.info(f"Processing completed in {elapsed_time:.2f} seconds")
logger.info(f"Average: {elapsed_time/page_count:.2f}s per page")
```

You can also retrieve timing from the result:

```python
result = ai_extractor.extract_from_multiple_images(...)
print(f"Total time: {result['processing_time']}s")
```

## Troubleshooting

### Issue: Parallel processing is slower than sequential

**Causes**:
- Too few pages (overhead exceeds benefit for <3 pages)
- API rate limiting kicking in
- Too many workers for available resources

**Solution**: Reduce `max_workers` or disable parallel processing

### Issue: API rate limit errors

**Error**: `429 Too Many Requests`

**Solution**: 
- Reduce `max_extraction_workers` (try 2-3)
- Add delays between batches
- Upgrade your API plan

### Issue: Memory issues with many workers

**Solution**: 
- Reduce `max_workers`
- Process smaller batches
- Increase system memory

## Technical Details

- **Threading Library**: `concurrent.futures.ThreadPoolExecutor`
- **Thread Safety**: Each worker has independent API connections
- **Result Ordering**: Results are sorted by page number after collection
- **Error Handling**: Individual page failures don't stop other pages
- **Resource Cleanup**: All threads are properly joined and cleaned up

## Future Enhancements

Planned improvements:
- [ ] Adaptive worker count based on document size
- [ ] Retry logic for failed pages
- [ ] Progress callbacks for UI integration
- [ ] Batch processing for very large documents
- [ ] GPU acceleration for image preprocessing
