#!/usr/bin/env python3
"""
Example script demonstrating how to use the Backup/Restore API.

This script shows how to:
1. Create a backup
2. List available backups
3. Download a backup
4. Restore from a backup

Requirements:
- Backend server running at http://localhost:8000
- Admin user credentials
"""

import argparse
import requests
import sys
from pathlib import Path


def get_admin_token(base_url: str, username: str, password: str) -> str:
    """Get an admin authentication token."""
    response = requests.post(
        f"{base_url}/auth/token",
        data={"username": username, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    response.raise_for_status()
    return response.json()["access_token"]


def create_backup(base_url: str, token: str) -> dict:
    """Create a new backup."""
    print("Creating backup...")
    response = requests.post(
        f"{base_url}/backups/create",
        headers={"Authorization": f"Bearer {token}"},
    )
    response.raise_for_status()
    backup = response.json()
    print(f"✓ Backup created: {backup['filename']}")
    print(f"  Size: {backup['size_bytes']:,} bytes")
    print(f"  Database records: {backup['database_records']}")
    print(f"  Neo4j nodes: {backup['neo4j_nodes']}")
    print(f"  Neo4j relationships: {backup['neo4j_relationships']}")
    return backup


def list_backups(base_url: str, token: str) -> list:
    """List all available backups."""
    print("\nListing backups...")
    response = requests.get(
        f"{base_url}/backups/",
        headers={"Authorization": f"Bearer {token}"},
    )
    response.raise_for_status()
    backups = response.json()
    if not backups:
        print("  No backups found")
    else:
        for i, backup in enumerate(backups, 1):
            print(f"  {i}. {backup['filename']}")
            print(f"     Size: {backup['size_bytes']:,} bytes")
            print(f"     Created: {backup['created_at']}")
    return backups


def download_backup(base_url: str, token: str, filename: str, output_dir: str = ".") -> Path:
    """Download a backup file."""
    print(f"\nDownloading {filename}...")
    response = requests.get(
        f"{base_url}/backups/{filename}/download",
        headers={"Authorization": f"Bearer {token}"},
    )
    response.raise_for_status()
    
    output_path = Path(output_dir) / filename
    with open(output_path, "wb") as f:
        f.write(response.content)
    
    print(f"✓ Downloaded to {output_path}")
    print(f"  Size: {output_path.stat().st_size:,} bytes")
    return output_path


def restore_backup(base_url: str, token: str, backup_file: Path) -> dict:
    """Restore from a backup file."""
    print(f"\n⚠️  WARNING: This will DELETE ALL EXISTING DATA!")
    confirm = input("Type 'yes' to continue: ")
    if confirm.lower() != "yes":
        print("Restore cancelled")
        return {}
    
    print(f"\nRestoring from {backup_file}...")
    with open(backup_file, "rb") as f:
        files = {"file": (backup_file.name, f, "application/gzip")}
        response = requests.post(
            f"{base_url}/backups/restore",
            headers={"Authorization": f"Bearer {token}"},
            files=files,
        )
        response.raise_for_status()
    
    result = response.json()
    print(f"✓ Restore completed successfully")
    print(f"  Status: {result['status']}")
    print(f"  Restored at: {result['restored_at']}")
    return result


def main():
    parser = argparse.ArgumentParser(description="Backup/Restore API Example")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="Base URL of the backend API",
    )
    parser.add_argument(
        "--username",
        required=True,
        help="Admin username",
    )
    parser.add_argument(
        "--password",
        required=True,
        help="Admin password",
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    
    # Create backup
    subparsers.add_parser("create", help="Create a new backup")
    
    # List backups
    subparsers.add_parser("list", help="List all backups")
    
    # Download backup
    download_parser = subparsers.add_parser("download", help="Download a backup")
    download_parser.add_argument("filename", help="Backup filename to download")
    download_parser.add_argument(
        "--output-dir",
        default=".",
        help="Output directory for downloaded backup",
    )
    
    # Restore backup
    restore_parser = subparsers.add_parser("restore", help="Restore from a backup")
    restore_parser.add_argument("backup_file", help="Path to backup file to restore")
    
    # All operations
    subparsers.add_parser(
        "demo",
        help="Demo: create backup, list, and download",
    )
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    try:
        # Get authentication token
        token = get_admin_token(args.base_url, args.username, args.password)
        print(f"✓ Authenticated as {args.username}")
        
        # Execute command
        if args.command == "create":
            create_backup(args.base_url, token)
        
        elif args.command == "list":
            list_backups(args.base_url, token)
        
        elif args.command == "download":
            download_backup(
                args.base_url,
                token,
                args.filename,
                args.output_dir,
            )
        
        elif args.command == "restore":
            restore_backup(args.base_url, token, Path(args.backup_file))
        
        elif args.command == "demo":
            # Create a backup
            backup = create_backup(args.base_url, token)
            
            # List all backups
            backups = list_backups(args.base_url, token)
            
            # Download the backup we just created
            download_backup(args.base_url, token, backup["filename"])
        
        return 0
    
    except requests.HTTPError as e:
        print(f"\n✗ HTTP Error: {e}", file=sys.stderr)
        if e.response is not None:
            try:
                error_detail = e.response.json().get("detail", "")
                if error_detail:
                    print(f"  Detail: {error_detail}", file=sys.stderr)
            except:
                pass
        return 1
    
    except Exception as e:
        print(f"\n✗ Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
