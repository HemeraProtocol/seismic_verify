#!/usr/bin/env python3
"""
Solidity Compiler S3 Sync Script
Downloads Linux solc compilers from official source and uploads to S3,
organized according to smart-contract-verifier-standalone project requirements
"""

import os
import sys
import json
import hashlib
import requests
import boto3
from pathlib import Path
import tempfile
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class SolcS3Syncer:
    def __init__(self, access_key: str, secret_key: str, region: str, bucket: str):
        """Initialize S3 syncer"""
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region
        )
        self.bucket = bucket
        self.base_url = "https://solc-bin.ethereum.org/linux-amd64"
        
    def fetch_version_list(self) -> List[Tuple[str, str]]:
        """Fetch official version list"""
        logger.info("📥 Fetching official Solidity version list...")
        try:
            response = requests.get(f"{self.base_url}/list.json", timeout=30)
            response.raise_for_status()
            data = response.json()
            builds = data.get('builds', [])
            # Use full version format: v0.8.30+commit.73712a01
            versions = [(f"v{build['longVersion']}", build['path']) for build in builds]
            logger.info(f"✅ Found {len(versions)} versions")
            return versions
        except Exception as e:
            logger.error(f"❌ Failed to fetch version list: {e}")
            raise

    def scan_local_compilers(self, local_dir: str) -> List[Tuple[str, str]]:
        """Scan local compiler files"""
        logger.info(f"📁 Scanning local compiler directory: {local_dir}")
        local_path = Path(local_dir)
        
        if not local_path.exists():
            logger.error(f"❌ Local directory does not exist: {local_dir}")
            raise FileNotFoundError(f"Local directory does not exist: {local_dir}")
        
        compilers = []
        
        # Scan for solc files directly in the directory
        if (local_path / "solc").exists():
            solc_file = local_path / "solc"
            version = self.get_solc_version(str(solc_file))
            if version:
                compilers.append((version, str(solc_file)))
                logger.info(f"✅ Found compiler: {solc_file}")
        
        # Scan all files
        for item in local_path.iterdir():
            if item.is_file() and (item.name == "solc" or item.name.startswith("solc")):
                # Skip already processed root directory solc file
                if item.name == "solc" and item.parent == local_path:
                    continue
                    
                version = self.get_solc_version(str(item))
                if version:
                    compilers.append((version, str(item)))
                    logger.info(f"✅ 找到编译器: {item}")
            elif item.is_dir():
                # Look for solc files in subdirectories
                solc_file = item / "solc"
                if solc_file.exists():
                    version = self.get_solc_version(str(solc_file))
                    if version:
                        compilers.append((version, str(solc_file)))
                        logger.info(f"✅ Found compiler: {solc_file}")
        
        logger.info(f"✅ Found {len(compilers)} local compilers")
        return compilers

    def get_solc_version(self, solc_path: str) -> str:
        """Get version info by executing solc --version"""
        try:
            # Ensure file has execute permissions
            os.chmod(solc_path, 0o755)
            
            # Execute solc --version
            result = subprocess.run([solc_path, '--version'], 
                                  capture_output=True, text=True, timeout=30)
            
            if result.returncode != 0:
                logger.error(f"❌ Failed to execute {solc_path} --version: {result.stderr}")
                return None
            
            # Parse version info, e.g.: Version: 0.8.29-develop.2025.9.18+commit.d4b8c7ae.Darwin.appleclang
            version_line = None
            for line in result.stdout.split('\n'):
                if 'Version:' in line:
                    version_line = line
                    break
            
            if not version_line:
                logger.error(f"❌ Unable to find version info from output: {result.stdout}")
                return None
            
            # Extract version number
            version_part = version_line.split('Version:')[1].strip()
            logger.info(f"🔍 Raw version info: {version_part}")
            
            # Parse complex version format, e.g.: 0.8.29-develop.2025.9.18+commit.d4b8c7ae.Darwin.appleclang
            if '+commit.' in version_part:
                # Separate main version and commit part
                main_part, commit_part = version_part.split('+commit.')
                
                # Extract main version (remove -develop suffix)
                if '-' in main_part:
                    main_version = main_part.split('-')[0]
                else:
                    main_version = main_part
                
                # Extract commit hash (remove platform info)
                commit_hash = commit_part.split('.')[0]
                
                # Combine final version
                version = f"v{main_version}+commit.{commit_hash}"
            else:
                # Handle cases without commit
                if '-' in version_part:
                    main_version = version_part.split('-')[0]
                else:
                    main_version = version_part.split('.')[0]
                version = f"v{main_version}"
            
            logger.info(f"✅ Detected version: {version}")
            return version
            
        except subprocess.TimeoutExpired:
            logger.error(f"❌ Execution timeout for {solc_path} --version")
            return None
        except Exception as e:
            logger.error(f"❌ Failed to get version info for {solc_path}: {e}")
            return None

    def check_s3_version_exists(self, version: str) -> bool:
        """Check if version already exists in S3"""
        try:
            self.s3_client.head_object(Bucket=self.bucket, Key=f"{version}/solc")
            return True
        except:
            return False

    def download_compiler(self, version: str, filename: str) -> Tuple[bytes, str]:
        """Download compiler and calculate SHA256 hash"""
        url = f"{self.base_url}/{filename}"
        logger.info(f"📥 Downloading {version}: {url}")
        
        try:
            response = requests.get(url, timeout=300)  # 5分钟超时
            response.raise_for_status()
            
            # Calculate SHA256 hash
            compiler_data = response.content
            sha256_hash = hashlib.sha256(compiler_data).hexdigest()
            
            logger.info(f"✅ Download completed {version} ({len(compiler_data)} bytes, hash: {sha256_hash[:16]}...)")
            return compiler_data, sha256_hash
            
        except Exception as e:
            logger.error(f"❌ Download failed {version}: {e}")
            raise

    def read_local_compiler(self, version: str, file_path: str) -> Tuple[bytes, str]:
        """Read local compiler file and calculate SHA256 hash"""
        logger.info(f"📁 Reading local compiler {version}: {file_path}")
        
        try:
            local_file = Path(file_path)
            if not local_file.exists():
                raise FileNotFoundError(f"Local file does not exist: {file_path}")
            
            # Read file content
            compiler_data = local_file.read_bytes()
            
            # Calculate SHA256 hash
            sha256_hash = hashlib.sha256(compiler_data).hexdigest()
            
            logger.info(f"✅ Read completed {version} ({len(compiler_data)} bytes, hash: {sha256_hash[:16]}...)")
            return compiler_data, sha256_hash
            
        except Exception as e:
            logger.error(f"❌ Failed to read local file {version}: {e}")
            raise

    def upload_to_s3(self, version: str, compiler_data: bytes, sha256_hash: str) -> bool:
        """Upload compiler and hash file to S3"""
        try:
            # Upload compiler file
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=f"{version}/solc",
                Body=compiler_data,
                ContentType='application/octet-stream'
            )
            
            # Upload hash file
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=f"{version}/sha256.hash",
                Body=sha256_hash.encode('utf-8'),
                ContentType='text/plain'
            )
            
            logger.info(f"✅ Upload completed {version}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Upload failed {version}: {e}")
            return False

    def process_version(self, version_data: Tuple[str, str], is_local: bool = False) -> bool:
        """Process single version"""
        version, filename_or_path = version_data
        
        # Check if already exists
        if self.check_s3_version_exists(version):
            logger.info(f"⏭️  Skipping existing version: {version}")
            return True
            
        try:
            if is_local:
                # Read local compiler
                compiler_data, sha256_hash = self.read_local_compiler(version, filename_or_path)
            else:
                # Download compiler
                compiler_data, sha256_hash = self.download_compiler(version, filename_or_path)
            
            # Upload to S3
            return self.upload_to_s3(version, compiler_data, sha256_hash)
            
        except Exception as e:
            logger.error(f"❌ Failed to process version {version}: {e}")
            return False

    def sync_all_versions(self, max_workers: int = 3, limit: int = None):
        """Sync all versions to S3"""
        logger.info("🚀 Starting to sync Solidity compilers to S3...")
        
        # Get version list
        versions = self.fetch_version_list()
        
        if limit:
            versions = versions[:limit]
            logger.info(f"🔢 Limiting processing to {limit} versions")
        
        # Concurrent processing
        success_count = 0
        failed_versions = []
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_version = {
                executor.submit(self.process_version, version_data, False): version_data[0] 
                for version_data in versions
            }
            
            for future in as_completed(future_to_version):
                version = future_to_version[future]
                try:
                    if future.result():
                        success_count += 1
                    else:
                        failed_versions.append(version)
                except Exception as e:
                    logger.error(f"❌ Version {version} processing exception: {e}")
                    failed_versions.append(version)
        
        # Output results
        logger.info(f"\n📊 Sync completed:")
        logger.info(f"   ✅ Success: {success_count}")
        logger.info(f"   ❌ Failed: {len(failed_versions)}")
        
        if failed_versions:
            logger.info(f"   Failed versions: {', '.join(failed_versions[:10])}")
            if len(failed_versions) > 10:
                logger.info(f"   ... and {len(failed_versions) - 10} more failed versions")

    def sync_local_compilers(self, local_dir: str, max_workers: int = 3):
        """Sync local compilers to S3"""
        logger.info(f"🚀 Starting to sync local compilers to S3: {local_dir}")
        
        # Scan local compilers
        compilers = self.scan_local_compilers(local_dir)
        
        if not compilers:
            logger.warning("⚠️  No local compiler files found")
            return
        
        # Concurrent processing
        success_count = 0
        failed_versions = []
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_version = {
                executor.submit(self.process_version, compiler_data, True): compiler_data[0] 
                for compiler_data in compilers
            }
            
            for future in as_completed(future_to_version):
                version = future_to_version[future]
                try:
                    if future.result():
                        success_count += 1
                    else:
                        failed_versions.append(version)
                except Exception as e:
                    logger.error(f"❌ Version {version} processing exception: {e}")
                    failed_versions.append(version)
        
        # Output results
        logger.info(f"\n📊 Local sync completed:")
        logger.info(f"   ✅ Success: {success_count}")
        logger.info(f"   ❌ Failed: {len(failed_versions)}")
        
        if failed_versions:
            logger.info(f"   Failed versions: {', '.join(failed_versions)}")

def main():
    """Main function"""
    # S3 configuration - from environment variables or modify directly here
    S3_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID", "AKIAX37LO3SFAHM5OVW3")
    S3_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "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")
    S3_REGION = os.getenv("AWS_REGION", "us-east-1")
    S3_BUCKET = os.getenv("S3_BUCKET", "solidity-public")
    
    # Parse command line arguments
    import argparse
    parser = argparse.ArgumentParser(description="Sync Solidity compilers to S3")
    parser.add_argument("--limit", type=int, help="Limit number of versions to process (for testing)")
    parser.add_argument("--workers", type=int, default=3, help="Number of concurrent workers (default 3)")
    parser.add_argument("--bucket", type=str, default=S3_BUCKET, help="S3 bucket name")
    parser.add_argument("--local-dir", type=str, help="Local compiler directory path (e.g.: /Users/user/solc_compiler)")
    args = parser.parse_args()
    
    # Validate S3 credentials
    if not all([S3_ACCESS_KEY, S3_SECRET_KEY]):
        logger.error("❌ Please set AWS credential environment variables: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY")
        sys.exit(1)
    
    try:
        # Create syncer
        syncer = SolcS3Syncer(S3_ACCESS_KEY, S3_SECRET_KEY, S3_REGION, args.bucket)
        
        if getattr(args, 'local_dir'):
            # Local mode: sync local compilers
            syncer.sync_local_compilers(args.local_dir, max_workers=args.workers)
        else:
            # Remote mode: sync from official repository
            syncer.sync_all_versions(max_workers=args.workers, limit=args.limit)
        
    except KeyboardInterrupt:
        logger.info("🛑 User interrupted sync")
    except Exception as e:
        logger.error(f"❌ Sync failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()