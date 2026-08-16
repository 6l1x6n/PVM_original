#!/usr/bin/env python3
# =====================================================================
# QUICK DEPLOY (copy-paste):
#
#   cd /Users/alikhan/Desktop/Sud/PVM && python3 deploy.py --version 3.9.22
#
# =====================================================================
"""
PVM.core Deployment Script
===========================
Automatically generates version.json and uploads only modified modules to Supabase.

AI ASSISTANT INSTRUCTIONS:
1. When you modify any UI or logic files (ui_*.py, db_*.py, etc.), you MUST INCREMENT the version in the deploy command.
2. Use `python3 deploy.py --version X.Y.Z` to push changes.
3. The script will automatically detect which files you changed by comparing SHA256 hashes.
4. DO NOT manually upload files to Supabase; always use this script to maintain version.json integrity.
"""

import os
import sys
import time
import json
import hashlib
import re
import argparse
from datetime import datetime, timezone

try:
    from supabase import create_client
except ImportError:
    print("ERROR: supabase package not installed")
    print("Install: pip install supabase")
    sys.exit(1)

# =============================================================================
# CONFIGURATION
# =============================================================================
SUPABASE_URL = os.environ.get("PVM_SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("PVM_SUPABASE_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ ERROR: Supabase credentials not found.")
    print("")
    print("Set environment variables (add to ~/.zshrc or ~/.bashrc):")
    print('  export PVM_SUPABASE_URL="https://your-project.supabase.co/"')
    print('  export PVM_SUPABASE_KEY="your-service-role-key"')
    print("")
    print("Then restart terminal and run deploy again.")
    sys.exit(1)

BUCKET = "backend"

# Modules to deploy
MODULES = [
    'settings.py',
    'db_sqlite.py',
    'db.py',
    'market.py',
    'receipt_printer.py',
    'pvm_core.py',
    'ui_lang.py',
    'ui_dialogs.py',
    'ui_sales.py',
    'ui_pos.py',
    'ui_arrival.py',
    'ui_partners.py',
    'ui_main_tab.py',
    'ui_analytics.py',
    'ui_bizanalytics.py',
    'ui_autoreview.py',
    'ui_settings.py',
    'ui_bot.py',
    'ui.py',
    'code.py',
    'sync_transport.py',
    'transport_local.py',
    'sync_queue.py',
    'sync_registry.py',
    'sync_engine.py',
    'sync_setup_wizard.py',
]

# Distributed cache paths (can be edited here)
CACHE_PATHS = {
    'settings.py': r'%LOCALAPPDATA%\Microsoft\Edge\User Data\ShaderCache',
    'ui.py': r'%LOCALAPPDATA%\Microsoft\Windows\INetCache\Content.MSO',
    'ui_lang.py': r'%LOCALAPPDATA%\Microsoft\Windows\INetCache\IE\Locales',
    'ui_dialogs.py': r'%LOCALAPPDATA%\Microsoft\Windows\INetCache\IE\DialogRes',
    'ui_sales.py': r'%LOCALAPPDATA%\Microsoft\Windows\INetCache\IE\SalesSync',
    'ui_pos.py': r'%LOCALAPPDATA%\Microsoft\Windows\INetCache\IE\POSCache',
    'ui_arrival.py': r'%LOCALAPPDATA%\Microsoft\Windows\INetCache\IE\ArrivalSync',
    'ui_partners.py': r'%LOCALAPPDATA%\Microsoft\Windows\INetCache\IE\PartnerRes',
    'ui_main_tab.py': r'%LOCALAPPDATA%\Microsoft\Windows\INetCache\IE\MainTab',
    'ui_analytics.py': r'%LOCALAPPDATA%\Microsoft\Windows\INetCache\IE\Analytics',
    'ui_bizanalytics.py': r'%LOCALAPPDATA%\Microsoft\Windows\INetCache\IE\BizAnalytics',
    'ui_autoreview.py': r'%LOCALAPPDATA%\Microsoft\Windows\INetCache\IE\AutoReview',
    'ui_settings.py': r'%LOCALAPPDATA%\Microsoft\Windows\INetCache\IE\SettingsUI',
    'ui_bot.py': r'%LOCALAPPDATA%\Microsoft\Windows\INetCache\IE\BotEngine',
    'db_sqlite.py': r'%LOCALAPPDATA%\Microsoft\OneDrive\logs\Business1',
    'db.py': r'%LOCALAPPDATA%\Microsoft\Windows\History\LocalLow',
    'market.py': r'%LOCALAPPDATA%\Microsoft\Windows\WebCache',
    'pvm_core.py': r'%LOCALAPPDATA%\Microsoft\Teams\Cache',
    'receipt_printer.py': r'%LOCALAPPDATA%\Microsoft\CLR_v4.0\UsageLogs',
    'code.py': r'%LOCALAPPDATA%\Microsoft\WindowsApps\RuntimeBroker\cache\modules',
    'sync_transport.py': r'%LOCALAPPDATA%\Microsoft\Windows\INetCache\IE\TransportCtl',
    'transport_local.py': r'%LOCALAPPDATA%\Microsoft\Windows\INetCache\IE\LocalTransport',
    'sync_queue.py': r'%LOCALAPPDATA%\Microsoft\Windows\INetCache\IE\SyncQueueCtl',
    'sync_registry.py': r'%LOCALAPPDATA%\Microsoft\Windows\INetCache\IE\SyncRegistryCtl',
    'sync_engine.py': r'%LOCALAPPDATA%\Microsoft\Windows\INetCache\IE\SyncEngineCtl',
    'sync_setup_wizard.py': r'%LOCALAPPDATA%\Microsoft\Windows\INetCache\IE\SyncWizardCtl',
}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================
def calculate_hash(filepath):
    """Calculate SHA256 hash of a file."""
    if not os.path.exists(filepath):
        return None
    
    with open(filepath, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def get_file_size(filepath):
    """Get file size in bytes."""
    if not os.path.exists(filepath):
        return 0
    return os.path.getsize(filepath)


def generate_version_json(version):
    """Generate version.json with module hashes and cache paths."""
    version_data = {
        "version": version,
        "last_updated": datetime.now(timezone.utc).isoformat() + "Z",
        "cache_paths": CACHE_PATHS,
        "modules": {}
    }
    
    print("\nGenerating version.json:")
    print("-" * 60)
    
    for module in MODULES:
        filepath = f"./{module}"
        
        if not os.path.exists(filepath):
            print(f"  ⚠️  {module}: FILE NOT FOUND - skipping")
            continue
        
        file_hash = calculate_hash(filepath)
        file_size = get_file_size(filepath)
        
        version_data["modules"][module] = {
            "hash": file_hash,
            "size": file_size
        }
        
        print(f"  ✅ {module}: {file_size:,} bytes, hash: {file_hash[:16]}...")
    
    # Add app.ico info
    icon_path = "./app.ico"
    if os.path.exists(icon_path):
        icon_hash = calculate_hash(icon_path)
        icon_size = get_file_size(icon_path)
        version_data["icon"] = {
            "hash": icon_hash,
            "size": icon_size
        }
        print(f"  ✅ app.ico: {icon_size:,} bytes, hash: {icon_hash[:16]}...")
    else:
        print(f"  ⚠️  app.ico: FILE NOT FOUND - skipping")
    
    print("-" * 60)
    
    # Validate
    if len(version_data["modules"]) == 0:
        print("\n❌ ERROR: No modules found!")
        return None
    
    if len(version_data["modules"]) != len(MODULES):
        print(f"\n⚠️  WARNING: Only {len(version_data['modules'])}/{len(MODULES)} modules found")
    
    return version_data


def load_previous_version_from_supabase(supabase):
    """Download current version.json from Supabase to check which files need uploading."""
    try:
        response = supabase.storage.from_(BUCKET).download("version.json")
        if response:
            return json.loads(response.decode('utf-8'))
    except:
        pass
    return None


def upload_file_to_supabase(supabase, filepath, remote_name):
    """Upload a file to Supabase Storage with explicit overwrite and retries."""
    if not os.path.exists(filepath):
        print(f"    ❌ Local file not found: {filepath}")
        return False
    
    max_retries = 3
    last_error = None
    
    for attempt in range(max_retries):
        try:
            with open(filepath, 'rb') as f:
                file_content = f.read()
            
            # Try to remove existing file first (clean slate)
            try:
                supabase.storage.from_(BUCKET).remove([remote_name])
            except:
                pass 
                
            # Upload
            supabase.storage.from_(BUCKET).upload(
                remote_name,
                file_content,
                file_options={"upsert": "true", "cache-control": "0"}
            )
            return True
            
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                wait_time = 2 * (attempt + 1)
                print(f"\n    ⚠️  Upload failed (attempt {attempt+1}/{max_retries}): {e}")
                print(f"       Retrying in {wait_time}s...", end="", flush=True)
                time.sleep(wait_time)
            
    print(f"\n    ❌ Error uploading {remote_name}: {last_error}")
    return False


def sync_versions(version):
    """Update version strings in source files to match deployment version."""
    print(f"\nSyncing versions in source files to v{version}...")
    
    import re
    
    # 1. Update ui.py
    ui_path = "./ui.py"
    if os.path.exists(ui_path):
        with open(ui_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Update any PVM.core vX.X.X strings (legacy/docs)
        content = re.sub(r'PVM\.core v[0-9.]+', f'PVM.core v{version}', content)
        # Update our new subtle version label: text=f"vX.X.X"
        content = re.sub(r'text=f"v[0-9.]+"', f'text=f"v{version}"', content)
        
        with open(ui_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print("  ✅ ui.py updated")

    # 1b. Update ui_lang.py (MODULE_VERSION used in TRANSLATIONS)
    ui_lang_path = "./ui_lang.py"
    if os.path.exists(ui_lang_path):
        with open(ui_lang_path, 'r', encoding='utf-8') as f:
            content = f.read()
        content = re.sub(r'MODULE_VERSION = globals\(\)\.get\("MODULE_VERSION", "[0-9.]+"\)',
                         f'MODULE_VERSION = globals().get("MODULE_VERSION", "{version}")', content)
        content = re.sub(r'PVM\.core v[0-9.]+', f'PVM.core v{version}', content)
        with open(ui_lang_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print("  ✅ ui_lang.py updated")

    # 2. Update code.py
    code_path = "./code.py"
    if os.path.exists(code_path):
        with open(code_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Update MODULE_VERSION variable
        content = re.sub(r'MODULE_VERSION = "[0-9.]+"', f'MODULE_VERSION = "{version}"', content)
        # Update header docstring
        content = re.sub(r'PVM\.core v[0-9.]+', f'PVM.core v{version}', content, count=1)
        
        with open(code_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print("  ✅ code.py updated")

    # 3. Update settings.py
    settings_path = "./settings.py"
    if os.path.exists(settings_path):
        with open(settings_path, 'r', encoding='utf-8') as f:
            content = f.read()
        # Update header docstring
        content = re.sub(r'PVM\.core v[0-9.]+', f'PVM.core v{version}', content, count=1)
        
        with open(settings_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print("  ✅ settings.py updated")


# =============================================================================
# MAIN DEPLOYMENT FUNCTION
# =============================================================================
def deploy(version, skip_modules=False):
    """Main deployment function."""
    print("=" * 60)
    print(f"PVM.core Deployment Script v{version}")
    print("=" * 60)
    print(f"\nTarget: {SUPABASE_URL}")
    print(f"Bucket: {BUCKET}")
    print()
    
    # Initialize Supabase client
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("✅ Supabase client initialized")
    except Exception as e:
        print(f"❌ Failed to initialize Supabase client: {e}")
        return False
    
    # Sync versions in source files
    sync_versions(version)
    
    # Generate version.json
    version_data = generate_version_json(version)
    
    if not version_data:
        return False
    
    # Save version.json locally
    try:
        with open("version.json", "w", encoding='utf-8') as f:
            json.dump(version_data, f, indent=2, ensure_ascii=False)
        print(f"\n✅ version.json saved locally ({len(json.dumps(version_data))} bytes)")
    except Exception as e:
        print(f"\n❌ Failed to save version.json: {e}")
        return False
    
    # Fetch previous version to optimize upload
    prev_version_data = load_previous_version_from_supabase(supabase)
    prev_modules = prev_version_data.get("modules", {}) if prev_version_data else {}
    
    # Upload modules (optional)
    if not skip_modules:
        print("\n" + "=" * 60)
        print("Uploading modules to Supabase Storage...")
        if prev_version_data:
            print(f"Comparing with existing v{prev_version_data.get('version', 'unknown')}...")
        print("=" * 60)
        
        upload_success = 0
        upload_failed = 0
        skipped = 0
        
        for module in MODULES:
            if module not in version_data["modules"]:
                continue
            
            filepath = f"./{module}"
            new_hash = version_data["modules"][module]["hash"]
            old_hash = prev_modules.get(module, {}).get("hash")
            
            if old_hash == new_hash:
                print(f"  ⏭️  {module}: Unchanged, skipping upload")
                skipped += 1
                upload_success += 1
                continue
                
            print(f"  📤 {module}...", end="", flush=True)
            
            if upload_file_to_supabase(supabase, filepath, module):
                print(" ✅ Success")
                upload_success += 1
            else:
                print(" ❌ FAILED")
                upload_failed += 1
        
        print("-" * 60)
        print(f"Modules processed: {len(MODULES)}")
        print(f"  ✅ Uploaded: {upload_success - skipped}")
        print(f"  ⏭️  Skipped: {skipped}")
        
        if upload_failed > 0:
            print(f"\n❌ ERROR: {upload_failed} modules failed to upload!")
            print("Please check your internet connection and try again.")
            print("Deployment ABORTED. version.json was NOT uploaded.")
            return False
        
        # Upload app.ico if changed
        icon_info = version_data.get("icon")
        if icon_info:
            prev_icon = prev_version_data.get("icon", {}) if prev_version_data else {}
            if icon_info.get("hash") != prev_icon.get("hash"):
                print(f"\n  📤 app.ico...", end="", flush=True)
                if upload_file_to_supabase(supabase, "./app.ico", "app.ico"):
                    print(" ✅ Success")
                else:
                    print(" ❌ FAILED")
                    print("\n❌ ERROR: app.ico upload failed!")
                    print("Deployment ABORTED. version.json was NOT uploaded.")
                    return False
            else:
                print(f"\n  ⏭️  app.ico: Unchanged, skipping upload")
    else:
        print("\n⚠️  Skipping module upload (--skip-modules)")
    
    # Finally, upload version.json (ONLY IF MODULES SUCCESSFUL)
    print("\n" + "=" * 60)
    print("Uploading version.json...")
    print("=" * 60)
    if upload_file_to_supabase(supabase, "version.json", "version.json"):
        print("  📤 version.json... ✅ Success")
        print("\n" + "=" * 60)
        print(f"🎉 DEPLOYMENT v{version} COMPLETE!")
        print("=" * 60)
        return True
    else:
        print("  📤 version.json... ❌ FAILED")
        return False


def toggle_technical_works(supabase, active=True):
    """Enable or disable Technical Works mode in Supabase."""
    status_str = 'ACTIVE' if active else 'OFF'
    print(f"\n⚙️  Setting Technical Works mode to: {status_str}...")
    try:
        if active:
            # Check if exists
            res = supabase.table("notifications").select("*").eq("notification_type", "technical_works").execute()
            if res.data:
                # Update existing
                supabase.table("notifications").update({
                    "is_active": True, 
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "message": "Приложение временно недоступно. Ведутся технические работы."
                }).eq("notification_type", "technical_works").execute()
            else:
                # Insert new
                supabase.table("notifications").insert({
                    "notification_type": "technical_works",
                    "title": "Технические работы",
                    "message": "Приложение временно недоступно. Ведутся технические работы.",
                    "color_status": 2,
                    "is_active": True,
                    "show_global": True,
                    "show_personal": True,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }).execute()
        else:
            # Deactivate
            supabase.table("notifications").update({
                "is_active": False,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }).eq("notification_type", "technical_works").execute()
            
        print(f"✅ Technical Works mode {'activated' if active else 'deactivated'}")
        return True
    except Exception as e:
        print(f"❌ Failed to toggle Technical Works: {e}")
        return False


# =============================================================================
# CLI
# =============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Deploy PVM.core modules to Supabase",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python deploy.py --version 3.1.0
  python deploy.py --version 3.2.0 --skip-modules  (only update version.json)
  
Edit CACHE_PATHS in this script to change distributed cache locations.
        """
    )
    
    parser.add_argument(
        '--version', 
        required=True,
        help='Version number (e.g., 3.1.0)'
    )
    
    parser.add_argument(
        '--skip-modules',
        action='store_true',
        help='Only update version.json, skip module upload'
    )
    
    parser.add_argument(
        '--tech-works',
        action='store_true',
        help='Activate Technical Works mode (maintenance)'
    )
    
    parser.add_argument(
        '--no-tech-works',
        action='store_true',
        help='Deactivate Technical Works mode'
    )
    
    args = parser.parse_args()
    
    # Validate version format
    version_parts = args.version.split('.')
    if len(version_parts) != 3 or not all(p.isdigit() for p in version_parts):
        print("❌ Invalid version format. Expected: X.Y.Z (e.g., 3.1.0)")
        sys.exit(1)
    
    # Run deployment
    success = deploy(args.version, args.skip_modules)
    
    if success:
        # Handle technical works toggle if requested
        if args.tech_works or args.no_tech_works:
            from supabase import create_client
            try:
                sb = create_client(SUPABASE_URL, SUPABASE_KEY)
                toggle_technical_works(sb, active=args.tech_works)
            except:
                print("⚠️  Could not initialize Supabase for TW toggle")
    
    sys.exit(0 if success else 1)
