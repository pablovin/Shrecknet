# Backup and Restore API Documentation

The Backup and Restore system provides a comprehensive way to create full backups of the Shrecknet backend_2 application and restore from those backups.

## Overview

The backup system captures:
- **All database tables** (Users, Games, Ontologies, Agents, Library, Notes, etc.)
- **All Neo4j graph data** (Nodes and relationships)
- **All media files** (Uploaded images, PDFs, etc.)

Backups are stored as `.tar.gz` archives in `/media/backups/` with timestamped filenames.

## Important Warnings

⚠️ **RESTORE IS DESTRUCTIVE**: The restore operation will DELETE ALL EXISTING DATA before restoring from the backup. Make sure you have a recent backup before performing a restore.

🔒 **ADMIN ONLY**: All backup endpoints require admin authentication.

## API Endpoints

### 1. Create Backup

**Endpoint:** `POST /backups/create`

**Description:** Creates a complete backup of all data and stores it in `/media/backups/`.

**Authentication:** Required (Admin role)

**Request:**
```bash
curl -X POST "http://localhost:8000/backups/create" \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN"
```

**Response:**
```json
{
  "filename": "backup_20231202_153045.tar.gz",
  "path": "/path/to/media/backups/backup_20231202_153045.tar.gz",
  "size_bytes": 15728640,
  "created_at": "20231202_153045",
  "database_records": 1523,
  "neo4j_nodes": 256,
  "neo4j_relationships": 412
}
```

**Response Fields:**
- `filename`: Name of the backup file
- `path`: Full path to the backup file on the server
- `size_bytes`: Size of the backup file in bytes
- `created_at`: Timestamp when the backup was created
- `database_records`: Total number of database records backed up
- `neo4j_nodes`: Number of Neo4j nodes backed up
- `neo4j_relationships`: Number of Neo4j relationships backed up

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

### 4. Restore Backup

**Endpoint:** `POST /backups/restore`

**Description:** Restores data from an uploaded backup file. **This will delete all existing data!**

**Authentication:** Required (Admin role)

**Request:**
```bash
curl -X POST "http://localhost:8000/backups/restore" \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  -F "file=@backup_20231202_153045.tar.gz"
```

**Response:**
```json
{
  "status": "success",
  "restored_at": "2023-12-02T16:45:30.123456",
  "backup_metadata": {
    "created_at": "20231202_153045",
    "database_records": 1523,
    "neo4j_nodes": 256,
    "neo4j_relationships": 412
  }
}
```

---

## Complete Workflow Examples

### Python Example

```python
import requests
from pathlib import Path

# Configuration
BASE_URL = "http://localhost:8000"
ADMIN_TOKEN = "your_admin_token_here"

headers = {
    "Authorization": f"Bearer {ADMIN_TOKEN}"
}

# 1. Create a backup
print("Creating backup...")
response = requests.post(f"{BASE_URL}/backups/create", headers=headers)
response.raise_for_status()
backup_info = response.json()
print(f"Backup created: {backup_info['filename']}")
print(f"Size: {backup_info['size_bytes']} bytes")
print(f"Records: {backup_info['database_records']}")

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
        result = response.json()
        print(f"Restore completed: {result['status']}")
        print(f"Restored at: {result['restored_at']}")
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
