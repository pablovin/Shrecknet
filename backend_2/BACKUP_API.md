# Backup and Restore API Documentation

The Backup and Restore system provides a comprehensive way to create full backups of the Shrecknet backend_2 application and restore from those backups **as background jobs**.

## Overview

The backup system captures:
- **All database tables** (Users, Games, Ontologies, Agents, Library, Notes, etc.)
- **All Neo4j graph data** (Nodes and relationships)
- **All media files** (Uploaded images, PDFs, etc.)

Backups are stored as `.tar.gz` archives in `/media/backups/` with timestamped filenames.

**🆕 NEW**: Backup and restore operations now run as **background jobs** using Celery, allowing you to monitor their progress and preventing request timeouts.

## Important Warnings

⚠️ **RESTORE IS DESTRUCTIVE**: The restore operation will DELETE ALL EXISTING DATA before restoring from the backup. Make sure you have a recent backup before performing a restore.

🔒 **ADMIN ONLY**: All backup endpoints require admin authentication.

✨ **ADMIN USER PRESERVED**: During restore, the admin user who invoked the restore operation is preserved and won't be replaced by backup data if a conflicting user exists in the backup.

## API Endpoints

### 1. Create Backup (Background Job)

**Endpoint:** `POST /backups/create`

**Description:** Creates a complete backup of all data as a background job. Returns immediately with job information.

**Authentication:** Required (Admin role)

**Request:**
```bash
curl -X POST "http://localhost:8000/backups/create" \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN"
```

**Response (202 Accepted):**
```json
{
  "celery_task_id": "abc-123-def-456",
  "status": "queued",
  "message": "Backup job created successfully. Monitor the background jobs to track progress."
}
```

**Response Fields:**
- `celery_task_id`: Celery task identifier for the background job
- `status`: Current status (queued, running, done, failed)
- `message`: Human-readable message about the job

**Monitoring Progress:**
Use the background jobs API to monitor the backup job:

```bash
# List all backup jobs
curl -X GET "http://localhost:8000/jobs/?job_type=backup" \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN"

# Get specific job details
curl -X GET "http://localhost:8000/jobs/{job_id}" \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN"
```

**Example Job Response:**
```json
{
  "id": 123,
  "celery_task_id": "abc-123-def-456",
  "author_type": "user",
  "author_id": "1",
  "job_type": "backup",
  "status": "done",
  "description": "Creating system backup",
  "details": {
    "admin_user_id": 1,
    "backup_filename": "backup_20231202_153045.tar.gz",
    "backup_size": 15728640,
    "database_records": 1523,
    "neo4j_nodes": 256,
    "neo4j_relationships": 412
  },
  "progress": 1.0,
  "error_message": null,
  "started_at": "2023-12-02T15:30:00.000000",
  "completed_at": "2023-12-02T15:30:45.000000",
  "duration_seconds": 45.2,
  "updated_at": "2023-12-02T15:30:45.000000"
}
```

---

### 2. List Backups

**Endpoint:** `GET /backups/`

**Description:** Lists all available backup files.

**Authentication:** Required (Admin role)

**Request:**
```bash
curl -X GET "http://localhost:8000/backups/" \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN"
```

**Response:**
```json
[
  {
    "filename": "backup_20231202_153045.tar.gz",
    "path": "/path/to/media/backups/backup_20231202_153045.tar.gz",
    "size_bytes": 15728640,
    "created_at": "2023-12-02T15:30:45.000000"
  },
  {
    "filename": "backup_20231201_090000.tar.gz",
    "path": "/path/to/media/backups/backup_20231201_090000.tar.gz",
    "size_bytes": 14985216,
    "created_at": "2023-12-01T09:00:00.000000"
  }
]
```

---

### 3. Download Backup

**Endpoint:** `GET /backups/{filename}/download`

**Description:** Downloads a specific backup file.

**Authentication:** Required (Admin role)

**Path Parameters:**
- `filename`: Name of the backup file (e.g., `backup_20231202_153045.tar.gz`)

**Request:**
```bash
curl -X GET "http://localhost:8000/backups/backup_20231202_153045.tar.gz/download" \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  -o backup_20231202_153045.tar.gz
```

**Response:** Binary file download (tar.gz archive)

---

### 4. Restore Backup (Background Job)

**Endpoint:** `POST /backups/restore`

**Description:** Restores data from an uploaded backup file as a background job. **This will delete all existing data!** The admin user who invokes the restore is preserved.

**Authentication:** Required (Admin role)

**Request:**
```bash
curl -X POST "http://localhost:8000/backups/restore" \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  -F "file=@backup_20231202_153045.tar.gz"
```

**Response (202 Accepted):**
```json
{
  "celery_task_id": "xyz-789-abc-012",
  "status": "queued",
  "message": "Restore job created successfully. Monitor the background jobs to track progress.",
  "temp_path": "/tmp/backup_20231202_153045.tar.gz"
}
```

**Response Fields:**
- `celery_task_id`: Celery task identifier for the background job
- `status`: Current status (queued, running, done, failed)
- `message`: Human-readable message about the job
- `temp_path`: Temporary path where the uploaded backup is stored

**Monitoring Progress:**
Use the background jobs API to monitor the restore job:

```bash
# List all restore jobs
curl -X GET "http://localhost:8000/jobs/?job_type=restore" \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN"

# Get specific job details
curl -X GET "http://localhost:8000/jobs/{job_id}" \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN"
```

**Example Job Response (Completed):**
```json
{
  "id": 124,
  "celery_task_id": "xyz-789-abc-012",
  "author_type": "user",
  "author_id": "1",
  "job_type": "restore",
  "status": "done",
  "description": "Restoring backup from backup_20231202_153045.tar.gz",
  "details": {
    "admin_user_id": 1,
    "backup_path": "/tmp/backup_20231202_153045.tar.gz",
    "restored_at": "2023-12-02T16:45:30.123456"
  },
  "progress": 1.0,
  "error_message": null,
  "started_at": "2023-12-02T16:30:00.000000",
  "completed_at": "2023-12-02T16:45:30.000000",
  "duration_seconds": 930.5,
  "updated_at": "2023-12-02T16:45:30.000000"
}
```

**Admin User Preservation:**
During restore, the admin user who invoked the restore operation is automatically preserved. If the backup contains a user with the same username or email as the admin user, that backup user is skipped, and the current admin user remains unchanged. This ensures you don't get locked out after a restore operation.

---

## Complete Workflow Examples

### Python Example

```python
import requests
import time
from pathlib import Path

# Configuration
BASE_URL = "http://localhost:8000"
ADMIN_TOKEN = "your_admin_token_here"

headers = {
    "Authorization": f"Bearer {ADMIN_TOKEN}"
}

# 1. Create a backup (returns immediately with job info)
print("Creating backup...")
response = requests.post(f"{BASE_URL}/backups/create", headers=headers)
response.raise_for_status()
backup_job = response.json()
print(f"Backup job created: {backup_job['celery_task_id']}")
print(f"Status: {backup_job['status']}")

# Monitor backup job progress
print("\nMonitoring backup progress...")
while True:
    # List recent backup jobs
    response = requests.get(
        f"{BASE_URL}/jobs/?job_type=backup&limit=1",
        headers=headers
    )
    response.raise_for_status()
    jobs = response.json()
    
    if jobs:
        job = jobs[0]
        print(f"Progress: {job['progress'] * 100:.1f}% - {job['status']}")
        
        if job['status'] in ['done', 'failed']:
            if job['status'] == 'done':
                print(f"Backup completed!")
                print(f"Filename: {job['details'].get('backup_filename')}")
                print(f"Size: {job['details'].get('backup_size')} bytes")
                print(f"Records: {job['details'].get('database_records')}")
            else:
                print(f"Backup failed: {job.get('error_message')}")
            break
    
    time.sleep(2)  # Wait 2 seconds before checking again

# 2. List all backups
print("\nListing all backups...")
response = requests.get(f"{BASE_URL}/backups/", headers=headers)
response.raise_for_status()
backups = response.json()
for backup in backups:
    print(f"  - {backup['filename']} ({backup['size_bytes']} bytes)")

# 3. Download the latest backup
if backups:
    latest_backup = backups[0]['filename']
    print(f"\nDownloading {latest_backup}...")
    response = requests.get(
        f"{BASE_URL}/backups/{latest_backup}/download",
        headers=headers
    )
    response.raise_for_status()
    
    with open(latest_backup, 'wb') as f:
        f.write(response.content)
    print(f"Downloaded to {latest_backup}")

# 4. Restore from a backup file
# WARNING: This will delete all existing data!
backup_file = Path("backup_20231202_153045.tar.gz")
if backup_file.exists():
    print(f"\nRestoring from {backup_file}...")
    with open(backup_file, 'rb') as f:
        files = {'file': f}
        response = requests.post(
            f"{BASE_URL}/backups/restore",
            headers=headers,
            files=files
        )
        response.raise_for_status()
        restore_job = response.json()
        print(f"Restore job created: {restore_job['celery_task_id']}")
    
    # Monitor restore job progress
    print("\nMonitoring restore progress...")
    while True:
        # List recent restore jobs
        response = requests.get(
            f"{BASE_URL}/jobs/?job_type=restore&limit=1",
            headers=headers
        )
        response.raise_for_status()
        jobs = response.json()
        
        if jobs:
            job = jobs[0]
            print(f"Progress: {job['progress'] * 100:.1f}% - {job['status']}")
            
            if job['status'] in ['done', 'failed']:
                if job['status'] == 'done':
                    print(f"Restore completed!")
                    print(f"Restored at: {job['details'].get('restored_at')}")
                else:
                    print(f"Restore failed: {job.get('error_message')}")
                break
        
        time.sleep(2)  # Wait 2 seconds before checking again
```

### JavaScript Example

```javascript
const BASE_URL = 'http://localhost:8000';
const ADMIN_TOKEN = 'your_admin_token_here';

const headers = {
  'Authorization': `Bearer ${ADMIN_TOKEN}`
};

// 1. Create a backup
async function createBackup() {
  const response = await fetch(`${BASE_URL}/backups/create`, {
    method: 'POST',
    headers: headers
  });
  
  const backup = await response.json();
  console.log('Backup created:', backup.filename);
  console.log('Size:', backup.size_bytes, 'bytes');
  console.log('Records:', backup.database_records);
  return backup;
}

// 2. List all backups
async function listBackups() {
  const response = await fetch(`${BASE_URL}/backups/`, {
    headers: headers
  });
  
  const backups = await response.json();
  backups.forEach(backup => {
    console.log(`- ${backup.filename} (${backup.size_bytes} bytes)`);
  });
  return backups;
}

// 3. Download a backup
async function downloadBackup(filename) {
  const response = await fetch(`${BASE_URL}/backups/${filename}/download`, {
    headers: headers
  });
  
  const blob = await response.blob();
  
  // Create download link
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  window.URL.revokeObjectURL(url);
  document.body.removeChild(a);
}

// 4. Restore from backup
async function restoreBackup(file) {
  const formData = new FormData();
  formData.append('file', file);
  
  const response = await fetch(`${BASE_URL}/backups/restore`, {
    method: 'POST',
    headers: headers,
    body: formData
  });
  
  const result = await response.json();
  console.log('Restore completed:', result.status);
  console.log('Restored at:', result.restored_at);
  return result;
}

// Usage
(async () => {
  // Create a backup
  await createBackup();
  
  // List backups
  const backups = await listBackups();
  
  // Download latest backup
  if (backups.length > 0) {
    await downloadBackup(backups[0].filename);
  }
})();
```

### cURL Examples

```bash
#!/bin/bash

# Configuration
BASE_URL="http://localhost:8000"
ADMIN_TOKEN="your_admin_token_here"

# 1. Create a backup
echo "Creating backup..."
curl -X POST "${BASE_URL}/backups/create" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  | jq '.'

# 2. List all backups
echo -e "\nListing backups..."
curl -X GET "${BASE_URL}/backups/" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  | jq '.'

# 3. Download a specific backup
BACKUP_FILE="backup_20231202_153045.tar.gz"
echo -e "\nDownloading ${BACKUP_FILE}..."
curl -X GET "${BASE_URL}/backups/${BACKUP_FILE}/download" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -o "${BACKUP_FILE}"

# 4. Restore from backup
echo -e "\nRestoring from backup..."
curl -X POST "${BASE_URL}/backups/restore" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -F "file=@${BACKUP_FILE}" \
  | jq '.'
```

---

## Backup File Structure

Each backup is a `.tar.gz` archive with the following structure:

```
backup_YYYYMMDD_HHMMSS/
├── metadata.json          # Backup metadata
├── database.json          # All database tables
├── neo4j.json            # Neo4j graph data
└── media/                # Media files
    ├── avatars/
    ├── library/
    └── ...
```

### metadata.json
```json
{
  "created_at": "20231202_153045",
  "database_records": 1523,
  "neo4j_nodes": 256,
  "neo4j_relationships": 412
}
```

### database.json
Contains all database tables with records in JSON format:
```json
{
  "users": [...],
  "ontologies": [...],
  "games": [...],
  "agents": [...],
  ...
}
```

### neo4j.json
Contains Neo4j nodes and relationships:
```json
{
  "nodes": [
    {
      "id": 123,
      "labels": ["Entity"],
      "properties": {...}
    }
  ],
  "relationships": [
    {
      "id": 456,
      "start_node_id": 123,
      "end_node_id": 124,
      "type": "RELATES_TO",
      "properties": {...}
    }
  ]
}
```

---

## Best Practices

1. **Regular Backups**: Schedule regular backups (daily or weekly) to ensure you can recover from data loss.

2. **Off-site Storage**: Download backups and store them in a separate location (cloud storage, external drive).

3. **Test Restores**: Periodically test restore operations in a non-production environment to ensure backups are valid.

4. **Pre-restore Backup**: Always create a fresh backup before performing a restore operation.

5. **Backup Naming**: The system automatically names backups with timestamps (`backup_YYYYMMDD_HHMMSS.tar.gz`) for easy identification.

6. **Disk Space**: Monitor available disk space in `/media/backups/` and delete old backups when needed.

---

## Automation Examples

### Daily Backup Script

```bash
#!/bin/bash
# daily_backup.sh - Run this with cron for automated backups

BASE_URL="http://localhost:8000"
ADMIN_TOKEN="your_admin_token_here"
BACKUP_DIR="/backups/offsite"

# Create backup
echo "Creating daily backup at $(date)"
RESPONSE=$(curl -s -X POST "${BASE_URL}/backups/create" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}")

FILENAME=$(echo $RESPONSE | jq -r '.filename')
echo "Created backup: ${FILENAME}"

# Download to offsite location
curl -X GET "${BASE_URL}/backups/${FILENAME}/download" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -o "${BACKUP_DIR}/${FILENAME}"

echo "Downloaded to ${BACKUP_DIR}/${FILENAME}"

# Clean up old backups (keep last 30 days)
find ${BACKUP_DIR} -name "backup_*.tar.gz" -mtime +30 -delete
echo "Cleaned up old backups"
```

Add to crontab for daily execution at 2 AM:
```bash
0 2 * * * /path/to/daily_backup.sh >> /var/log/daily_backup.log 2>&1
```

---

## Troubleshooting

### Error: "Failed to create backup"
- Check disk space in `/media/backups/`
- Verify database connection is healthy
- Check Neo4j connection
- Review server logs for detailed error messages

### Error: "Backup not found"
- Verify the backup filename is correct
- Check that the file exists in `/media/backups/`
- Ensure you have proper file permissions

### Error: "Failed to restore backup"
- Verify the backup file is valid and not corrupted
- Ensure sufficient disk space
- Check database and Neo4j connections
- Review server logs for detailed error messages

### Restore takes a long time
- Large backups with many media files can take time
- Neo4j restoration is done node-by-node and relationship-by-relationship
- Be patient and monitor server logs for progress

---

## Security Considerations

1. **Admin Only**: All backup operations require admin authentication to prevent unauthorized access to sensitive data.

2. **Backups Directory**: The `/media/backups/` directory is excluded from new backups to prevent infinite recursion.

3. **Sensitive Data**: Backups contain all user data including hashed passwords. Store backups securely.

4. **File Upload Size**: Ensure your server is configured to handle large file uploads if restoring from large backups.

---

## Support

For issues or questions about the backup system, please contact the development team or file an issue in the repository.
