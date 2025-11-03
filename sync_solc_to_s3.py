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
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple

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
        
    def fetch_version_list(self) -> List[Tuple[str, str]]:
        """获取官方版本列表"""
        logger.info("📥 获取官方Solidity版本列表...")
        try:
            response = requests.get(f"{self.base_url}/list.json", timeout=30)
            response.raise_for_status()
            data = response.json()
            builds = data.get('builds', [])
            # 使用完整版本号格式：v0.8.30+commit.73712a01
            versions = [(f"v{build['longVersion']}", build['path']) for build in builds]
            logger.info(f"✅ 找到 {len(versions)} 个版本")
            return versions
        except Exception as e:
            logger.error(f"❌ 获取版本列表失败: {e}")
            raise

    def scan_local_compilers(self, local_dir: str) -> List[Tuple[str, str]]:
        """扫描本地编译器文件"""
        logger.info(f"📁 扫描本地编译器目录: {local_dir}")
        local_path = Path(local_dir)
        
        if not local_path.exists():
            logger.error(f"❌ 本地目录不存在: {local_dir}")
            raise FileNotFoundError(f"本地目录不存在: {local_dir}")
        
        compilers = []
        
        # 扫描直接在目录下的 solc 文件
        if (local_path / "solc").exists():
            solc_file = local_path / "solc"
            version = self.get_solc_version(str(solc_file))
            if version:
                compilers.append((version, str(solc_file)))
                logger.info(f"✅ 找到编译器: {solc_file}")
        
        # 扫描所有文件
        for item in local_path.iterdir():
            if item.is_file() and (item.name == "solc" or item.name.startswith("solc")):
                # 跳过已经处理过的根目录 solc 文件
                if item.name == "solc" and item.parent == local_path:
                    continue
                    
                version = self.get_solc_version(str(item))
                if version:
                    compilers.append((version, str(item)))
                    logger.info(f"✅ 找到编译器: {item}")
            elif item.is_dir():
                # 查找子目录中的 solc 文件
                solc_file = item / "solc"
                if solc_file.exists():
                    version = self.get_solc_version(str(solc_file))
                    if version:
                        compilers.append((version, str(solc_file)))
                        logger.info(f"✅ 找到编译器: {solc_file}")
        
        logger.info(f"✅ 本地找到 {len(compilers)} 个编译器")
        return compilers

    def get_solc_version(self, solc_path: str) -> str:
        """通过执行solc --version获取版本信息"""
        try:
            # 确保文件有执行权限
            os.chmod(solc_path, 0o755)
            
            # 执行 solc --version
            result = subprocess.run([solc_path, '--version'], 
                                  capture_output=True, text=True, timeout=30)
            
            if result.returncode != 0:
                logger.error(f"❌ 执行 {solc_path} --version 失败: {result.stderr}")
                return None
            
            # 解析版本信息，例如：Version: 0.8.29-develop.2025.9.18+commit.d4b8c7ae.Darwin.appleclang
            version_line = None
            for line in result.stdout.split('\n'):
                if 'Version:' in line:
                    version_line = line
                    break
            
            if not version_line:
                logger.error(f"❌ 无法从版本输出中找到版本信息: {result.stdout}")
                return None
            
            # 提取版本号
            version_part = version_line.split('Version:')[1].strip()
            logger.info(f"🔍 原始版本信息: {version_part}")
            
            # 解析复杂版本格式，如：0.8.29-develop.2025.9.18+commit.d4b8c7ae.Darwin.appleclang
            if '+commit.' in version_part:
                # 分离主版本号和commit部分
                main_part, commit_part = version_part.split('+commit.')
                
                # 提取主版本号（去除-develop等后缀）
                if '-' in main_part:
                    main_version = main_part.split('-')[0]
                else:
                    main_version = main_part
                
                # 提取commit hash（去除平台信息）
                commit_hash = commit_part.split('.')[0]
                
                # 组合最终版本
                version = f"v{main_version}+commit.{commit_hash}"
            else:
                # 处理没有commit的情况
                if '-' in version_part:
                    main_version = version_part.split('-')[0]
                else:
                    main_version = version_part.split('.')[0]
                version = f"v{main_version}"
            
            logger.info(f"✅ 检测到版本: {version}")
            return version
            
        except subprocess.TimeoutExpired:
            logger.error(f"❌ 执行 {solc_path} --version 超时")
            return None
        except Exception as e:
            logger.error(f"❌ 获取版本信息失败 {solc_path}: {e}")
            return None

    def check_s3_version_exists(self, version: str) -> bool:
        """检查S3中是否已存在该版本"""
        try:
            self.s3_client.head_object(Bucket=self.bucket, Key=f"{version}/solc")
            return True
        except:
            return False

    def download_compiler(self, version: str, filename: str) -> Tuple[bytes, str]:
        """下载编译器并计算SHA256哈希"""
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

    def read_local_compiler(self, version: str, file_path: str) -> Tuple[bytes, str]:
        """读取本地编译器文件并计算SHA256哈希"""
        logger.info(f"📁 读取本地编译器 {version}: {file_path}")
        
        try:
            local_file = Path(file_path)
            if not local_file.exists():
                raise FileNotFoundError(f"本地文件不存在: {file_path}")
            
            # 读取文件内容
            compiler_data = local_file.read_bytes()
            
            # 计算SHA256哈希
            sha256_hash = hashlib.sha256(compiler_data).hexdigest()
            
            logger.info(f"✅ 读取完成 {version} ({len(compiler_data)} bytes, hash: {sha256_hash[:16]}...)")
            return compiler_data, sha256_hash
            
        except Exception as e:
            logger.error(f"❌ 读取本地文件失败 {version}: {e}")
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

    def process_version(self, version_data: Tuple[str, str], is_local: bool = False) -> bool:
        """处理单个版本"""
        version, filename_or_path = version_data
        
        # 检查是否已存在
        if self.check_s3_version_exists(version):
            logger.info(f"⏭️  跳过已存在的版本: {version}")
            return True
            
        try:
            if is_local:
                # 读取本地编译器
                compiler_data, sha256_hash = self.read_local_compiler(version, filename_or_path)
            else:
                # 下载编译器
                compiler_data, sha256_hash = self.download_compiler(version, filename_or_path)
            
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

    def sync_local_compilers(self, local_dir: str, max_workers: int = 3):
        """同步本地编译器到S3"""
        logger.info(f"🚀 开始同步本地编译器到S3: {local_dir}")
        
        # 扫描本地编译器
        compilers = self.scan_local_compilers(local_dir)
        
        if not compilers:
            logger.warning("⚠️  未找到任何本地编译器文件")
            return
        
        # 并发处理
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
                    logger.error(f"❌ 版本 {version} 处理异常: {e}")
                    failed_versions.append(version)
        
        # 输出结果
        logger.info(f"\n📊 本地同步完成:")
        logger.info(f"   ✅ 成功: {success_count}")
        logger.info(f"   ❌ 失败: {len(failed_versions)}")
        
        if failed_versions:
            logger.info(f"   失败版本: {', '.join(failed_versions)}")

def main():
    """主函数"""
    # S3配置 - 从环境变量或直接修改这里
    S3_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID", "AKIAX37LO3SFAHM5OVW3")
    S3_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "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")
    S3_REGION = os.getenv("AWS_REGION", "us-east-1")
    S3_BUCKET = os.getenv("S3_BUCKET", "solidity-public")
    
    # 解析命令行参数
    import argparse
    parser = argparse.ArgumentParser(description="同步Solidity编译器到S3")
    parser.add_argument("--limit", type=int, help="限制处理的版本数量（用于测试）")
    parser.add_argument("--workers", type=int, default=3, help="并发数量（默认3）")
    parser.add_argument("--bucket", type=str, default=S3_BUCKET, help="S3 bucket名称")
    parser.add_argument("--local-dir", type=str, help="本地编译器目录路径（例如：/Users/user/solc_compiler）")
    args = parser.parse_args()
    
    # 验证S3凭证
    if not all([S3_ACCESS_KEY, S3_SECRET_KEY]):
        logger.error("❌ 请设置AWS凭证环境变量: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY")
        sys.exit(1)
    
    try:
        # 创建同步器
        syncer = SolcS3Syncer(S3_ACCESS_KEY, S3_SECRET_KEY, S3_REGION, args.bucket)
        
        if getattr(args, 'local_dir'):
            # 本地模式：同步本地编译器
            syncer.sync_local_compilers(args.local_dir, max_workers=args.workers)
        else:
            # 远程模式：从官方仓库同步
            syncer.sync_all_versions(max_workers=args.workers, limit=args.limit)
        
    except KeyboardInterrupt:
        logger.info("🛑 用户中断同步")
    except Exception as e:
        logger.error(f"❌ 同步失败: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()