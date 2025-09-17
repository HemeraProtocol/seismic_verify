#!/usr/bin/env python3
"""
Solidity编译器S3同步脚本
从官方源下载Linux版solc编译器并上传到S3，按照smart-contract-verifier-standalone项目要求的格式组织
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Tuple

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class SolcS3Syncer:
    def __init__(self, access_key: str, secret_key: str, region: str, bucket: str):
        """初始化S3同步器"""
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region
        )
        self.bucket = bucket
        self.base_url = "https://solc-bin.ethereum.org/linux-amd64"
        
    def fetch_version_list(self) -> List[Dict]:
        """获取官方版本列表"""
        logger.info("📥 获取官方Solidity版本列表...")
        try:
            response = requests.get(f"{self.base_url}/list.json", timeout=30)
            response.raise_for_status()
            data = response.json()
            versions = data.get('releases', {})
            logger.info(f"✅ 找到 {len(versions)} 个版本")
            return list(versions.items())
        except Exception as e:
            logger.error(f"❌ 获取版本列表失败: {e}")
            raise

    def check_s3_version_exists(self, version: str) -> bool:
        """检查S3中是否已存在该版本"""
        try:
            self.s3_client.head_object(Bucket=self.bucket, Key=f"{version}/solc")
            return True
        except:
            return False

    def download_compiler(self, version: str, filename: str) -> Tuple[bytes, str]:
        """下载编译器并计算哈希"""
        url = f"{self.base_url}/{filename}"
        logger.info(f"📥 下载 {version}: {url}")
        
        try:
            response = requests.get(url, timeout=300)  # 5分钟超时
            response.raise_for_status()
            
            # 计算SHA256哈希
            compiler_data = response.content
            sha256_hash = hashlib.sha256(compiler_data).hexdigest()
            
            logger.info(f"✅ 下载完成 {version} ({len(compiler_data)} bytes, hash: {sha256_hash[:16]}...)")
            return compiler_data, sha256_hash
            
        except Exception as e:
            logger.error(f"❌ 下载失败 {version}: {e}")
            raise

    def upload_to_s3(self, version: str, compiler_data: bytes, sha256_hash: str) -> bool:
        """上传编译器和哈希文件到S3"""
        try:
            # 上传编译器文件
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=f"{version}/solc",
                Body=compiler_data,
                ContentType='application/octet-stream'
            )
            
            # 上传哈希文件
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=f"{version}/sha256.hash",
                Body=sha256_hash.encode('utf-8'),
                ContentType='text/plain'
            )
            
            logger.info(f"✅ 上传完成 {version}")
            return True
            
        except Exception as e:
            logger.error(f"❌ 上传失败 {version}: {e}")
            return False

    def process_version(self, version_data: Tuple[str, str]) -> bool:
        """处理单个版本"""
        version, filename = version_data
        
        # 检查是否已存在
        if self.check_s3_version_exists(version):
            logger.info(f"⏭️  跳过已存在的版本: {version}")
            return True
            
        try:
            # 下载编译器
            compiler_data, sha256_hash = self.download_compiler(version, filename)
            
            # 上传到S3
            return self.upload_to_s3(version, compiler_data, sha256_hash)
            
        except Exception as e:
            logger.error(f"❌ 处理版本 {version} 失败: {e}")
            return False

    def sync_all_versions(self, max_workers: int = 3, limit: int = None):
        """同步所有版本到S3"""
        logger.info("🚀 开始同步Solidity编译器到S3...")
        
        # 获取版本列表
        versions = self.fetch_version_list()
        
        if limit:
            versions = versions[:limit]
            logger.info(f"🔢 限制处理版本数量: {limit}")
        
        # 并发处理
        success_count = 0
        failed_versions = []
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_version = {
                executor.submit(self.process_version, version_data): version_data[0] 
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
                    logger.error(f"❌ 版本 {version} 处理异常: {e}")
                    failed_versions.append(version)
        
        # 输出结果
        logger.info(f"\n📊 同步完成:")
        logger.info(f"   ✅ 成功: {success_count}")
        logger.info(f"   ❌ 失败: {len(failed_versions)}")
        
        if failed_versions:
            logger.info(f"   失败版本: {', '.join(failed_versions[:10])}")
            if len(failed_versions) > 10:
                logger.info(f"   ... 还有 {len(failed_versions) - 10} 个失败版本")

def main():
    """主函数"""
    # S3配置 - 从环境变量或直接修改这里
    S3_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID", "AKIAX37LO3SFHDGA6I7R")
    S3_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "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")
    S3_REGION = os.getenv("AWS_REGION", "us-east-1")
    S3_BUCKET = os.getenv("S3_BUCKET", "seismic-solidity")
    
    # 解析命令行参数
    import argparse
    parser = argparse.ArgumentParser(description="同步Solidity编译器到S3")
    parser.add_argument("--limit", type=int, help="限制处理的版本数量（用于测试）")
    parser.add_argument("--workers", type=int, default=3, help="并发数量（默认3）")
    parser.add_argument("--bucket", type=str, default=S3_BUCKET, help="S3 bucket名称")
    args = parser.parse_args()
    
    # 验证S3凭证
    if not all([S3_ACCESS_KEY, S3_SECRET_KEY]):
        logger.error("❌ 请设置AWS凭证环境变量: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY")
        sys.exit(1)
    
    try:
        # 创建同步器并执行同步
        syncer = SolcS3Syncer(S3_ACCESS_KEY, S3_SECRET_KEY, S3_REGION, args.bucket)
        syncer.sync_all_versions(max_workers=args.workers, limit=args.limit)
        
    except KeyboardInterrupt:
        logger.info("🛑 用户中断同步")
    except Exception as e:
        logger.error(f"❌ 同步失败: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()