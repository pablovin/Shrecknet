"""
Tests for backup and restore functionality.
"""

import json
import tarfile
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.models.user import UserRole


@pytest.mark.asyncio
async def test_create_backup_requires_admin(client: AsyncClient, user_token: str):
    """Test that creating a backup requires admin role."""
    headers = {"Authorization": f"Bearer {user_token}"}
    response = await client.post("/backups/create", headers=headers)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_create_backup_success(client: AsyncClient, admin_token: str):
    """Test creating a backup successfully."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    response = await client.post("/backups/create", headers=headers)
    assert response.status_code == 201
    
    data = response.json()
    assert "filename" in data
    assert "path" in data
    assert "size_bytes" in data
    assert "created_at" in data
    assert "database_records" in data
    assert "neo4j_nodes" in data
    assert "neo4j_relationships" in data
    assert data["filename"].startswith("backup_")
    assert data["filename"].endswith(".tar.gz")


@pytest.mark.asyncio
async def test_list_backups_requires_admin(client: AsyncClient, user_token: str):
    """Test that listing backups requires admin role."""
    headers = {"Authorization": f"Bearer {user_token}"}
    response = await client.get("/backups/", headers=headers)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_backups_empty(client: AsyncClient, admin_token: str):
    """Test listing backups when there are none."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    response = await client.get("/backups/", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_list_backups_with_existing(client: AsyncClient, admin_token: str):
    """Test listing backups after creating one."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    # Create a backup first
    create_response = await client.post("/backups/create", headers=headers)
    assert create_response.status_code == 201
    created_backup = create_response.json()
    
    # List backups
    list_response = await client.get("/backups/", headers=headers)
    assert list_response.status_code == 200
    backups = list_response.json()
    assert isinstance(backups, list)
    assert len(backups) >= 1
    
    # Find our created backup in the list
    found = False
    for backup in backups:
        if backup["filename"] == created_backup["filename"]:
            found = True
            assert "size_bytes" in backup
            assert "created_at" in backup
            break
    assert found, "Created backup not found in list"


@pytest.mark.asyncio
async def test_download_backup_requires_admin(client: AsyncClient, user_token: str, admin_token: str):
    """Test that downloading a backup requires admin role."""
    # Create a backup as admin
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    create_response = await client.post("/backups/create", headers=admin_headers)
    assert create_response.status_code == 201
    backup = create_response.json()
    
    # Try to download as regular user
    user_headers = {"Authorization": f"Bearer {user_token}"}
    response = await client.get(
        f"/backups/{backup['filename']}/download",
        headers=user_headers
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_download_backup_not_found(client: AsyncClient, admin_token: str):
    """Test downloading a non-existent backup."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    response = await client.get(
        "/backups/nonexistent_backup.tar.gz/download",
        headers=headers
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_download_backup_success(client: AsyncClient, admin_token: str):
    """Test downloading a backup successfully."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    # Create a backup
    create_response = await client.post("/backups/create", headers=headers)
    assert create_response.status_code == 201
    backup = create_response.json()
    
    # Download the backup
    download_response = await client.get(
        f"/backups/{backup['filename']}/download",
        headers=headers
    )
    assert download_response.status_code == 200
    assert download_response.headers["content-type"] == "application/gzip"
    assert len(download_response.content) > 0


@pytest.mark.asyncio
async def test_backup_contains_expected_data(client: AsyncClient, admin_token: str):
    """Test that a backup contains the expected data structure."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    # Create a backup
    create_response = await client.post("/backups/create", headers=headers)
    assert create_response.status_code == 201
    backup = create_response.json()
    
    # Download the backup
    download_response = await client.get(
        f"/backups/{backup['filename']}/download",
        headers=headers
    )
    assert download_response.status_code == 200
    
    # Save to temp file and extract
    temp_backup = Path("/tmp") / backup["filename"]
    with open(temp_backup, "wb") as f:
        f.write(download_response.content)
    
    # Extract and verify contents
    temp_extract = Path("/tmp") / "test_extract"
    temp_extract.mkdir(exist_ok=True)
    
    try:
        with tarfile.open(temp_backup, "r:gz") as tar:
            tar.extractall(temp_extract)
        
        # Find the backup directory
        backup_dirs = [d for d in temp_extract.iterdir() if d.is_dir()]
        assert len(backup_dirs) == 1
        backup_dir = backup_dirs[0]
        
        # Check for expected files
        assert (backup_dir / "metadata.json").exists()
        assert (backup_dir / "database.json").exists()
        assert (backup_dir / "neo4j.json").exists()
        
        # Verify metadata structure
        with open(backup_dir / "metadata.json", "r") as f:
            metadata = json.load(f)
            assert "created_at" in metadata
            assert "database_records" in metadata
            assert "neo4j_nodes" in metadata
            assert "neo4j_relationships" in metadata
        
        # Verify database structure
        with open(backup_dir / "database.json", "r") as f:
            database = json.load(f)
            assert isinstance(database, dict)
            assert "users" in database
            assert isinstance(database["users"], list)
        
        # Verify Neo4j structure
        with open(backup_dir / "neo4j.json", "r") as f:
            neo4j_data = json.load(f)
            assert "nodes" in neo4j_data
            assert "relationships" in neo4j_data
            assert isinstance(neo4j_data["nodes"], list)
            assert isinstance(neo4j_data["relationships"], list)
    
    finally:
        # Cleanup
        if temp_backup.exists():
            temp_backup.unlink()
        if temp_extract.exists():
            import shutil
            shutil.rmtree(temp_extract)


@pytest.mark.asyncio
async def test_restore_backup_requires_admin(client: AsyncClient, user_token: str):
    """Test that restoring a backup requires admin role."""
    headers = {"Authorization": f"Bearer {user_token}"}
    
    # Create a dummy file
    dummy_file = Path("/tmp/dummy_backup.tar.gz")
    with tarfile.open(dummy_file, "w:gz") as tar:
        pass
    
    try:
        with open(dummy_file, "rb") as f:
            files = {"file": ("dummy_backup.tar.gz", f, "application/gzip")}
            response = await client.post(
                "/backups/restore",
                headers=headers,
                files=files
            )
            assert response.status_code == 403
    finally:
        if dummy_file.exists():
            dummy_file.unlink()


@pytest.mark.asyncio
async def test_restore_backup_invalid_format(client: AsyncClient, admin_token: str):
    """Test restoring with an invalid file format."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    # Create a text file (not tar.gz)
    dummy_file = Path("/tmp/dummy.txt")
    with open(dummy_file, "w") as f:
        f.write("not a backup")
    
    try:
        with open(dummy_file, "rb") as f:
            files = {"file": ("dummy.txt", f, "text/plain")}
            response = await client.post(
                "/backups/restore",
                headers=headers,
                files=files
            )
            assert response.status_code == 400
            assert "Invalid file format" in response.json()["detail"]
    finally:
        if dummy_file.exists():
            dummy_file.unlink()


@pytest.mark.asyncio
async def test_backup_and_restore_roundtrip(client: AsyncClient, admin_token: str):
    """Test creating a backup and restoring it."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    # Create some test data first
    # Create a game
    game_data = {
        "name": "Test Game for Backup",
        "ontology_id": 1,  # Assuming ontology exists from fixtures
    }
    game_response = await client.post("/games/", json=game_data, headers=headers)
    # If this fails, it's okay - we're just trying to add some data
    
    # Create a backup
    create_response = await client.post("/backups/create", headers=headers)
    assert create_response.status_code == 201
    backup = create_response.json()
    
    # Download the backup
    download_response = await client.get(
        f"/backups/{backup['filename']}/download",
        headers=headers
    )
    assert download_response.status_code == 200
    
    # Save backup to temp file
    temp_backup = Path("/tmp") / backup["filename"]
    with open(temp_backup, "wb") as f:
        f.write(download_response.content)
    
    try:
        # Restore from the backup
        with open(temp_backup, "rb") as f:
            files = {"file": (backup["filename"], f, "application/gzip")}
            restore_response = await client.post(
                "/backups/restore",
                headers=headers,
                files=files
            )
            assert restore_response.status_code == 200
            restore_data = restore_response.json()
            assert restore_data["status"] == "success"
            assert "restored_at" in restore_data
            assert "backup_metadata" in restore_data
    
    finally:
        if temp_backup.exists():
            temp_backup.unlink()


@pytest.mark.asyncio
async def test_backup_excludes_backup_directory(client: AsyncClient, admin_token: str):
    """Test that backups don't include the backups directory itself."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    # Create first backup
    create_response1 = await client.post("/backups/create", headers=headers)
    assert create_response1.status_code == 201
    
    # Create second backup (should not include first backup)
    create_response2 = await client.post("/backups/create", headers=headers)
    assert create_response2.status_code == 201
    backup2 = create_response2.json()
    
    # Download second backup
    download_response = await client.get(
        f"/backups/{backup2['filename']}/download",
        headers=headers
    )
    assert download_response.status_code == 200
    
    # Extract and verify backups directory is not included
    temp_backup = Path("/tmp") / backup2["filename"]
    with open(temp_backup, "wb") as f:
        f.write(download_response.content)
    
    temp_extract = Path("/tmp") / "test_exclude_extract"
    temp_extract.mkdir(exist_ok=True)
    
    try:
        with tarfile.open(temp_backup, "r:gz") as tar:
            # Get all file names in the archive
            file_names = tar.getnames()
            
            # Verify no files from backups directory are included
            for name in file_names:
                assert "backups/" not in name, f"Backup directory should be excluded but found: {name}"
    
    finally:
        if temp_backup.exists():
            temp_backup.unlink()
        if temp_extract.exists():
            import shutil
            shutil.rmtree(temp_extract)
