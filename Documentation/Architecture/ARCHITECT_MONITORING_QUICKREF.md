# Architect Job Monitoring - Quick Reference for Frontend

This is a quick reference guide for frontend developers to integrate Architect job monitoring.

## Key Endpoints

### Starting Jobs

```bash
# Step 1: Start Analysis
POST /api/jobs/architect/{agent_id}/analyze
Body: {
  "ontology_instance_id": "my-story",
  "max_chunks": 50,
  "chunk_size": 1024
}
Returns: {
  "id": "<run_id>",
  "background_job_id": <analysis_job_id>,
  "status": "pending"
}
```

```bash
# Step 2: Start Generation (after validation)
POST /api/jobs/architect/runs/{run_id}/generate
Body: {
  "run_id": "<run_id>",
  "validated_proposals": [...],
  "author_type": "user",
  "author_id": "<user_id>"
}
Returns: {
  "status": "accepted",
  "task_id": "<celery_task_id>"
}
```

### Monitoring Jobs

```bash
# Get specific job status and progress
GET /api/jobs/{job_id}
Returns: {
  "id": <job_id>,
  "status": "running",  # queued, running, done, failed
  "progress": 0.65,     # 0.0 to 1.0
  "error_message": null
}
```

```bash
# Get architect run with proposals
GET /api/jobs/architect/runs/{run_id}
Returns: {
  "id": "<run_id>",
  "background_job_id": <analysis_job_id>,
  "generation_job_id": <generation_job_id>,
  "status": "completed",
  "proposals": [...]
}
```

```bash
# List all architect runs for an agent
GET /api/jobs/architect/{agent_id}/runs?limit=20&offset=0
Returns: [
  {
    "id": "<run_id>",
    "background_job_id": <analysis_job_id>,
    "generation_job_id": <generation_job_id>,
    "status": "completed",
    "created_at": "2024-11-02T10:30:00Z"
  }
]
```

```bash
# List all jobs (with optional filtering)
GET /api/jobs/?job_type=architect_analysis&status=running
Returns: [
  {
    "kind": "architect_analysis",
    "job_id": "42",
    "status": "running",
    "progress": 0.65
  }
]
```

## Example React Hook

```typescript
import { useState, useEffect } from 'react';

interface ArchitectJob {
  id: number;
  status: 'queued' | 'running' | 'done' | 'failed';
  progress: number;
  error_message?: string;
  details?: any;
}

export function useArchitectJobMonitor(jobId: number | null) {
  const [job, setJob] = useState<ArchitectJob | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!jobId) return;

    const interval = setInterval(async () => {
      try {
        const response = await fetch(`/api/jobs/${jobId}`);
        const data = await response.json();
        setJob(data);

        // Stop polling when job is done or failed
        if (data.status === 'done' || data.status === 'failed') {
          clearInterval(interval);
        }
      } catch (err) {
        setError(err.message);
        clearInterval(interval);
      }
    }, 2000);

    return () => clearInterval(interval);
  }, [jobId]);

  return { job, error };
}

// Usage:
function ArchitectMonitor({ runId }) {
  const [run, setRun] = useState(null);
  
  // Fetch initial run data
  useEffect(() => {
    fetch(`/api/jobs/architect/runs/${runId}`)
      .then(r => r.json())
      .then(setRun);
  }, [runId]);

  // Monitor analysis job
  const { job: analysisJob } = useArchitectJobMonitor(run?.background_job_id);
  
  // Monitor generation job (if started)
  const { job: generationJob } = useArchitectJobMonitor(run?.generation_job_id);

  return (
    <div>
      <h3>Analysis: {analysisJob?.status}</h3>
      <ProgressBar value={analysisJob?.progress ?? 0} />
      
      {run?.generation_job_id && (
        <>
          <h3>Generation: {generationJob?.status}</h3>
          <ProgressBar value={generationJob?.progress ?? 0} />
        </>
      )}
    </div>
  );
}
```

## Example Python Client

```python
import time
import requests

def monitor_architect_workflow(base_url: str, agent_id: str, instance_id: str):
    """Complete example of monitoring an architect workflow."""
    
    # Step 1: Start analysis
    response = requests.post(
        f"{base_url}/api/jobs/architect/{agent_id}/analyze",
        json={
            "ontology_instance_id": instance_id,
            "max_chunks": 50
        }
    )
    run = response.json()
    run_id = run["id"]
    analysis_job_id = run["background_job_id"]
    
    print(f"Started analysis: run_id={run_id}, job_id={analysis_job_id}")
    
    # Step 2: Poll analysis job
    while True:
        response = requests.get(f"{base_url}/api/jobs/{analysis_job_id}")
        job = response.json()
        
        print(f"Analysis: {job['status']} ({job['progress']*100:.0f}%)")
        
        if job["status"] == "done":
            print("Analysis complete!")
            break
        elif job["status"] == "failed":
            print(f"Analysis failed: {job['error_message']}")
            return
        
        time.sleep(2)
    
    # Step 3: Get proposals
    response = requests.get(f"{base_url}/api/jobs/architect/runs/{run_id}")
    run_data = response.json()
    proposals = run_data["proposals"]
    
    print(f"Got {len(proposals)} proposals")
    
    # Step 4: Validate proposals (in real app, show to user)
    validated = []
    for p in proposals:
        validated.append({
            "proposal_id": p["id"],
            "status": "approved",  # or "rejected"
            "corrected_alias": None,
            "corrected_entity_definition_id": None,
            "corrected_proposal_type": None,
            "corrected_entity_instance_id": None,
            "merged_into_proposal_id": None
        })
    
    # Step 5: Start generation
    response = requests.post(
        f"{base_url}/api/jobs/architect/runs/{run_id}/generate",
        json={
            "run_id": run_id,
            "validated_proposals": validated,
            "author_type": "user",
            "author_id": "1"
        }
    )
    
    # Get generation job ID
    time.sleep(1)  # Wait a moment for job to attach
    response = requests.get(f"{base_url}/api/jobs/architect/runs/{run_id}")
    run_data = response.json()
    generation_job_id = run_data["generation_job_id"]
    
    print(f"Started generation: job_id={generation_job_id}")
    
    # Step 6: Poll generation job
    while True:
        response = requests.get(f"{base_url}/api/jobs/{generation_job_id}")
        job = response.json()
        
        print(f"Generation: {job['status']} ({job['progress']*100:.0f}%)")
        
        if job["status"] == "done":
            details = job["details"]
            print(f"Generation complete!")
            print(f"  Created: {details.get('created_entities', 0)} entities")
            print(f"  Updated: {details.get('updated_entities', 0)} entities")
            break
        elif job["status"] == "failed":
            print(f"Generation failed: {job['error_message']}")
            return
        
        time.sleep(2)

# Usage
if __name__ == "__main__":
    monitor_architect_workflow(
        base_url="http://localhost:8000",
        agent_id="agent-123",
        instance_id="my-story"
    )
```

## Polling Best Practices

1. **Interval**: 2-5 seconds is optimal
   - Too fast: wastes server resources
   - Too slow: poor user experience

2. **Timeout**: Set a maximum polling duration
   ```javascript
   const MAX_POLL_TIME = 15 * 60 * 1000; // 15 minutes
   const startTime = Date.now();
   
   const interval = setInterval(() => {
     if (Date.now() - startTime > MAX_POLL_TIME) {
       clearInterval(interval);
       console.error('Job timeout');
     }
     // ... poll job
   }, 2000);
   ```

3. **Error Recovery**: Allow retry on network errors
   ```javascript
   let retries = 0;
   const MAX_RETRIES = 3;
   
   try {
     const response = await fetch(`/api/jobs/${jobId}`);
     retries = 0; // Reset on success
   } catch (err) {
     retries++;
     if (retries >= MAX_RETRIES) {
       clearInterval(interval);
     }
   }
   ```

4. **Cleanup**: Always clear intervals
   ```javascript
   useEffect(() => {
     const interval = setInterval(pollJob, 2000);
     return () => clearInterval(interval); // Cleanup
   }, [jobId]);
   ```

## Job Filtering Examples

```bash
# Get all running architect jobs
GET /api/jobs/?job_type=architect_analysis&status=running

# Get all jobs for a specific ontology
GET /api/jobs/?ontology_id=1&status=done

# Get recent jobs by a specific user
GET /api/jobs/?author_type=user&author_id=123&limit=50
```

## Understanding Job Details

Each job includes a `details` field with context-specific information:

### Analysis Job Details
```json
{
  "run_id": "<run_id>",
  "agent_id": "<agent_id>",
  "ontology_instance_id": "<instance_id>",
  "status": "Chunking story text",
  "proposal_count": 12,
  "chunk_count": 35
}
```

### Generation Job Details
```json
{
  "run_id": "<run_id>",
  "proposal_count": 12,
  "status": "Generating 5 new entities",
  "created_entities": 5,
  "updated_entities": 7
}
```

## Error Handling

When `status === 'failed'`, check the `error_message` field:

```javascript
if (job.status === 'failed') {
  console.error('Job failed:', job.error_message);
  
  // Common errors:
  // - "Agent not found"
  // - "Architect analysis run not found"
  // - "OpenAI API error: ..."
  // - "Database error: ..."
  
  // Allow user to retry or start a new run
  showRetryDialog(job.error_message);
}
```

## Complete Monitoring Flow

```
1. POST /api/jobs/architect/{agent_id}/analyze
   ↓
2. Poll GET /api/jobs/{background_job_id}
   ↓ (when status=done)
3. GET /api/jobs/architect/runs/{run_id}
   ↓ (user validates proposals)
4. POST /api/jobs/architect/runs/{run_id}/generate
   ↓
5. GET /api/jobs/architect/runs/{run_id} (to get generation_job_id)
   ↓
6. Poll GET /api/jobs/{generation_job_id}
   ↓ (when status=done)
7. Done! Entities created/updated
```

For complete API documentation, see [ARCHITECT_MONITORING.md](./ARCHITECT_MONITORING.md).
