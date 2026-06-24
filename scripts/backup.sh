#!/bin/bash
# Scrapower daily backup — tarball data/ → ~/backups/
# Run via cron: 0 3 * * * /home/ubuntu/scrapower/scripts/backup.sh

set -e
BACKUP_DIR="/home/ubuntu/backups"
DATA_DIR="/home/ubuntu/scrapower/data"
RETENTION_DAYS=7

mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/scrapower_$TIMESTAMP.tar.gz"

# Backup (exclude whisper-cache which is re-downloadable)
tar czf "$BACKUP_FILE" -C "$DATA_DIR" --exclude='whisper-cache' .

echo "[$(date)] Backup: $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"

# Cleanup old backups
find "$BACKUP_DIR" -name 'scrapower_*.tar.gz' -mtime +$RETENTION_DAYS -delete
echo "[$(date)] Cleanup: removed backups older than $RETENTION_DAYS days"
