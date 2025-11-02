# Architect Job Monitoring Guide

This document explains how to monitor Architect analysis and entity generation jobs in the Shrecknet platform.

## Overview

The Architect workflow consists of two main steps:
1. **Step 1 (Analysis)**: Analyzes text and proposes entities to create/update
2. **Step 2 (Generation)**: Creates/updates entities based on validated proposals

Both steps run as asynchronous background jobs using Celery, allowing the API to return immediately while processing continues in the background.

## Monitoring Architecture

Each architect run tracks TWO background jobs:
- `background_job_id`: The analysis job (Step 1)
- `generation_job_id`: The generation job (Step 2)

Jobs are tracked in the `background_jobs` table with real-time progress updates.

## API Endpoints for Monitoring

### 1. Start an Architect Analysis (Step 1)

**Endpoint**: `POST /api/jobs/architect/{agent_id}/analyze`

**Request Body**:
```json
{
  "ontology_instance_id": "my-story-instance",
  "ontology_id": 1,
  "max_chunks": 50,
  "chunk_size": 1024
}
```

**Response** (202 Accepted):
```json
{
  "id": "bbb67cfa-4012-4716-8112-dbf5521ec3e2",
  "agent_id": "agent-123",
  "background_job_id": 42,
  "generation_job_id": null,
  "ontology_id": 1,
  "ontology_instance_id": "my-story-instance",
  "status": "pending",
  "input_chunk_count": null,
  "settings": {
    "requested_by": 1,
    "max_chunks": 50,
    "chunk_size": 1024
  },
  "created_at": "2024-11-02T10:30:00Z",
  "updated_at": "2024-11-02T10:30:00Z",
  "proposals": []
}
```

**Key fields**:
- `id`: The architect run ID (use this to track the entire workflow)
- `background_job_id`: The job ID for step 1 (analysis)
- `status`: Current status of the run (`pending`, `running`, `completed`, `failed`)

### 2. Monitor Analysis Progress

**Endpoint**: `GET /api/jobs/{job_id}`

**Example**: `GET /api/jobs/42`

**Response**:
```json
{
  "id": 42,
  "celery_task_id": "abc123...",
  "author_type": "user",
  "author_id": "1",
  "job_type": "architect_analysis",
  "status": "running",
  "description": "Architect analysis for agent agent-123 on instance my-story-instance",
  "details": {
    "run_id": "bbb67cfa-4012-4716-8112-dbf5521ec3e2",
    "agent_id": "agent-123",
    "ontology_instance_id": "my-story-instance"
  },
  "progress": 0.65,
  "error_message": null,
  "ontology_id": 1,
  "started_at": "2024-11-02T10:30:00Z",
  "completed_at": null,
  "duration_seconds": null,
  "updated_at": "2024-11-02T10:30:45Z"
}
```

**Key fields**:
- `progress`: Decimal from 0.0 to 1.0 (e.g., 0.65 = 65% complete)
- `status`: `queued`, `running`, `done`, or `failed`
- `error_message`: Error details if `status` is `failed`
- `details`: Additional context (contains `run_id` to link back to architect run)

### 3. Get Analysis Results

**Endpoint**: `GET /api/jobs/architect/runs/{run_id}`

**Example**: `GET /api/jobs/architect/runs/bbb67cfa-4012-4716-8112-dbf5521ec3e2`

**Response**:
```json
{
  "id": "bbb67cfa-4012-4716-8112-dbf5521ec3e2",
  "agent_id": "agent-123",
  "background_job_id": 42,
  "generation_job_id": null,
  "ontology_id": 1,
  "ontology_instance_id": "my-story-instance",
  "status": "completed",
  "input_chunk_count": 35,
  "settings": {...},
  "created_at": "2024-11-02T10:30:00Z",
  "updated_at": "2024-11-02T10:32:00Z",
  "proposals": [
    {
      "id": "prop-001",
      "proposal_type": "new_instance",
      "status": "pending",
      "entity_definition_id": 5,
      "entity_instance_id": null,
      "alias": "Gandalf",
      "confidence": 0.92,
      "justification": "Mentioned as a wizard character...",
      "evidence": [...],
      "chunks": ["chunk1", "chunk2"],
      "created_at": "2024-11-02T10:31:00Z",
      "updated_at": "2024-11-02T10:31:00Z"
    }
  ]
}
```

**Key fields**:
- `status`: Should be `completed` when analysis finishes
- `proposals`: Array of proposed entities (step 2 input)
- `input_chunk_count`: Number of text chunks analyzed

### 4. Start Entity Generation (Step 2)

**Endpoint**: `POST /api/jobs/architect/runs/{run_id}/generate`

**Request Body**:
```json
{
  "run_id": "bbb67cfa-4012-4716-8112-dbf5521ec3e2",
  "validated_proposals": [
    {
      "proposal_id": "prop-001",
      "status": "approved",
      "corrected_alias": null,
      "corrected_entity_definition_id": null,
      "corrected_proposal_type": null,
      "corrected_entity_instance_id": null,
      "merged_into_proposal_id": null
    }
  ],
  "author_type": "user",
  "author_id": "1"
}
```

**Response** (202 Accepted):
```json
{
  "status": "accepted",
  "task_id": "def456...",
  "run_id": "bbb67cfa-4012-4716-8112-dbf5521ec3e2",
  "message": "Entity generation task started"
}
```

### 5. Monitor Generation Progress

After step 2 starts, get the run details again to find the `generation_job_id`:

**Endpoint**: `GET /api/jobs/architect/runs/{run_id}`

The response will now include:
```json
{
  "id": "bbb67cfa-4012-4716-8112-dbf5521ec3e2",
  "background_job_id": 42,
  "generation_job_id": 43,
  ...
}
```

Then monitor the generation job:

**Endpoint**: `GET /api/jobs/43`

**Response**:
```json
{
  "id": 43,
  "job_type": "architect_generation",
  "status": "running",
  "description": "Architect entity generation for run bbb67cfa-4012-4716-8112-dbf5521ec3e2",
  "progress": 0.75,
  "details": {
    "run_id": "bbb67cfa-4012-4716-8112-dbf5521ec3e2",
    "proposal_count": 12
  },
  ...
}
```

### 6. List All Jobs (with Filtering)

**Endpoint**: `GET /api/jobs/`

**Query Parameters**:
- `author_type`: Filter by `user` or `agent`
- `author_id`: Filter by specific author
- `job_type`: Filter by job type (`architect_analysis`, `architect_generation`, etc.)
- `status`: Filter by status (`queued`, `running`, `done`, `failed`)
- `ontology_id`: Filter by ontology
- `limit`: Max results (default 100, max 1000)
- `offset`: Pagination offset

**Example**: `GET /api/jobs/?job_type=architect_analysis&status=running&limit=20`

**Response**:
```json
[
  {
    "kind": "architect_analysis",
    "job_id": "42",
    "start_time": "2024-11-02T10:30:00Z",
    "status": "running",
    "author_type": "user",
    "author_id": "1",
    "description": "Architect analysis for agent...",
    "details": {...},
    "progress": 0.65,
    "error_message": null,
    "completed_at": null,
    "duration_seconds": null,
    "ontology_id": 1,
    "updated_at": "2024-11-02T10:30:45Z"
  }
]
```

### 7. List Architect Runs for an Agent

**Endpoint**: `GET /api/jobs/architect/{agent_id}/runs`

**Query Parameters**:
- `limit`: Max results (default 20, max 100)
- `offset`: Pagination offset

**Example**: `GET /api/jobs/architect/agent-123/runs?limit=10`

**Response**:
```json
[
  {
    "id": "bbb67cfa-4012-4716-8112-dbf5521ec3e2",
    "agent_id": "agent-123",
    "background_job_id": 42,
    "generation_job_id": 43,
    "ontology_id": 1,
    "ontology_instance_id": "my-story-instance",
    "status": "completed",
    "input_chunk_count": 35,
    "created_at": "2024-11-02T10:30:00Z",
    "updated_at": "2024-11-02T10:35:00Z"
  }
]
```

## Frontend Integration Example

Here's a complete flow for monitoring an architect job:

```javascript
// Step 1: Start analysis
const analysisResponse = await fetch('/api/jobs/architect/agent-123/analyze', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({
    ontology_instance_id: 'my-story',
    max_chunks: 50
  })
});
const run = await analysisResponse.json();
const runId = run.id;
const analysisJobId = run.background_job_id;

// Step 2: Poll analysis job until complete
const pollAnalysis = setInterval(async () => {
  const jobResponse = await fetch(`/api/jobs/${analysisJobId}`);
  const job = await jobResponse.json();
  
  console.log(`Analysis progress: ${(job.progress * 100).toFixed(0)}%`);
  
  if (job.status === 'done') {
    clearInterval(pollAnalysis);
    console.log('Analysis complete!');
    
    // Get proposals
    const runResponse = await fetch(`/api/jobs/architect/runs/${runId}`);
    const runWithProposals = await runResponse.json();
    
    // Show proposals to user for validation
    showProposalsToUser(runWithProposals.proposals);
  } else if (job.status === 'failed') {
    clearInterval(pollAnalysis);
    console.error('Analysis failed:', job.error_message);
  }
}, 2000); // Poll every 2 seconds

// Step 3: After user validates, start generation
async function startGeneration(validatedProposals) {
  const genResponse = await fetch(`/api/jobs/architect/runs/${runId}/generate`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      run_id: runId,
      validated_proposals: validatedProposals,
      author_type: 'user',
      author_id: currentUser.id
    })
  });
  
  // Get the generation job ID
  const runResponse = await fetch(`/api/jobs/architect/runs/${runId}`);
  const updatedRun = await runResponse.json();
  const generationJobId = updatedRun.generation_job_id;
  
  // Poll generation job
  const pollGeneration = setInterval(async () => {
    const jobResponse = await fetch(`/api/jobs/${generationJobId}`);
    const job = await jobResponse.json();
    
    console.log(`Generation progress: ${(job.progress * 100).toFixed(0)}%`);
    
    if (job.status === 'done') {
      clearInterval(pollGeneration);
      console.log('Generation complete!');
      console.log('Created entities:', job.details.created_entities);
      console.log('Updated entities:', job.details.updated_entities);
    } else if (job.status === 'failed') {
      clearInterval(pollGeneration);
      console.error('Generation failed:', job.error_message);
    }
  }, 2000);
}
```

## Job Status Lifecycle

### Analysis Job (Step 1)
1. `queued` → Task is queued in Celery
2. `running` → Task is executing
   - Progress updates from 0.05 to 0.95
3. `done` → Analysis complete, proposals generated
   - OR `failed` → Error occurred

### Generation Job (Step 2)
1. `queued` → Task is queued in Celery
2. `running` → Task is executing
   - 0.05: Processing validated proposals
   - 0.10-0.20: Loading ontology data
   - 0.35-0.50: Generating new entities
   - 0.70-0.85: Updating existing entities
   - 0.95: Finalizing
3. `done` → Entity generation complete
   - OR `failed` → Error occurred

## Error Handling

If a job fails:
1. Check `error_message` field in the job details
2. The architect run `status` will remain in its last state (won't automatically mark as failed)
3. Re-run the workflow by creating a new run

## Best Practices

1. **Polling Interval**: Poll every 2-5 seconds to balance responsiveness and API load
2. **Timeout**: Set a maximum polling duration (e.g., 15 minutes) to handle stuck jobs
3. **User Feedback**: Show progress percentage and current step description to users
4. **Error Recovery**: Allow users to retry failed jobs or start new runs
5. **Cleanup**: Delete old completed/failed jobs to prevent database bloat

## Additional Endpoints

### Delete Completed/Failed Jobs

**Endpoint**: `DELETE /api/jobs/`

**Request Body**:
```json
{
  "jobs": [
    {"kind": "architect_analysis", "job_id": "42"},
    {"kind": "architect_generation", "job_id": "43"}
  ]
}
```

**Response**:
```json
{
  "deleted_count": 2
}
```

Note: Only jobs with status `done` or `failed` can be deleted.
