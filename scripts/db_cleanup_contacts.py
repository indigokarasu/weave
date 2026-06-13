#!/usr/bin/env python3
"""
Google Contacts Field Mapping Cleanup Script
Fixes:
1. URLs with type "url" -> proper types ("Website" or "LinkedIn")
2. Duplicate LinkedIn URLs -> keeps the most complete one
3. UserDefined organization fields -> removes them (they're duplicates)
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

AGENT_ROOT = Path(os.environ.get("AGENT_ROOT", Path.home() / ".hermes"))

# Use MCP credentials directory
CREDS_DIR = Path('/root/.google_Google services/credentials')
TOKEN_PATH = CREDS_DIR / 'google-workspace-user.json'

# Add the Google Workspace skill scripts to path
sys.path.insert(0, str(AGENT_ROOT / 'skills/productivity/google-workspace/scripts'))

# Set up environment
os.environ['HERMES_HOME'] = str(AGENT_ROOT)

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

class GoogleContactsCleanup:
    def __init__(self, backup_dir=None):
        self.backup_dir = Path(backup_dir) if backup_dir else AGENT_ROOT / "backups/google_contacts"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        # Load credentials from MCP directory
        with open(TOKEN_PATH) as f:
            token_data = json.load(f)
        
        self.creds = Credentials(
            token=token_data.get('token'),
            refresh_token=token_data.get('refresh_token'),
            token_uri='https://oauth2.googleapis.com/token',
            client_id=token_data['client_id'],
            client_secret=<GOOGLE_OAUTH_CLIENT_SECRET>['client_secret'],
            scopes=token_data.get('scopes', []),
        )
        
        self.service = build('people', 'v1', credentials=self.creds)
        self.stats = {
            'total_contacts': 0,
            'contacts_with_url_issues': 0,
            'contacts_with_duplicate_linkedin': 0,
            'contacts_with_userdefined_org': 0,
            'urls_fixed': 0,
            'duplicate_linkedin_removed': 0,
            'userdefined_org_removed': 0,
            'errors': []
        }
    
    def backup_contacts(self, contacts):
        """Create a backup of contacts before modification."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = self.backup_dir / f"contacts_backup_{timestamp}.json"
        
        backup_data = {
            'timestamp': timestamp,
            'contact_count': len(contacts),
            'contacts': contacts
        }
        
        with open(backup_file, 'w') as f:
            json.dump(backup_data, f, indent=2)
        
        print(f"Backup created: {backup_file}")
        return backup_file
    
    def get_all_contacts(self):
        """Fetch all contacts with all fields."""
        contacts = []
        page_token = None
        
        while True:
            resp = self.service.people().connections().list(
                resourceName='people/me',
                pageSize=100,
                personFields='names,urls,userDefined,organizations,metadata',
                pageToken=page_token
            ).execute()
            
            connections = resp.get('connections', [])
            contacts.extend(connections)
            
            page_token = resp.get('nextPageToken')
            if not page_token:
                break
        
        self.stats['total_contacts'] = len(contacts)
        return contacts
    
    def analyze_contact(self, contact):
        """Analyze a contact for issues and return fixes needed."""
        issues = {
            'url_fixes': [],
            'duplicate_linkedin': [],
            'userdefined_org': [],
            'needs_update': False
        }
        
        resource_name = contact.get('resourceName', '')
        name = (contact.get('names') or [{}])[0].get('displayName', 'Unknown')
        
        # 1. Check URL issues
        urls = contact.get('urls', [])
        for i, url in enumerate(urls):
            url_type = url.get('type', '')
            url_value = url.get('value', '')
            
            if url_type.lower() == 'url':
                # Determine proper type
                if 'linkedin.com' in url_value.lower():
                    proper_type = 'LinkedIn'
                else:
                    proper_type = 'Website'
                
                issues['url_fixes'].append({
                    'index': i,
                    'current_type': url_type,
                    'proper_type': proper_type,
                    'value': url_value
                })
        
        # 2. Check for duplicate LinkedIn URLs
        linkedin_urls = []
        for i, url in enumerate(urls):
            if 'linkedin.com' in url.get('value', '').lower():
                linkedin_urls.append({
                    'index': i,
                    'value': url.get('value', ''),
                    'type': url.get('type', ''),
                    'metadata': url.get('metadata', {})
                })
        
        if len(linkedin_urls) > 1:
            # Find the best LinkedIn URL (prefer www.linkedin.com over others)
            best_index = 0
            best_score = 0
            
            for i, linkedin in enumerate(linkedin_urls):
                score = 0
                value = linkedin['value'].lower()
                
                # Prefer www.linkedin.com
                if 'www.linkedin.com' in value:
                    score += 10
                # Prefer URLs with /in/ path
                if '/in/' in value:
                    score += 5
                # Prefer longer/more complete URLs
                score += len(value) / 100
                
                if score > best_score:
                    best_score = score
                    best_index = i
            
            # Mark others for removal
            for i, linkedin in enumerate(linkedin_urls):
                if i != best_index:
                    issues['duplicate_linkedin'].append({
                        'index': linkedin['index'],
                        'value': linkedin['value'],
                        'reason': 'duplicate' if i != best_index else 'kept'
                    })
        
        # 3. Check for userDefined organization fields
        user_defined = contact.get('userDefined', [])
        for i, ud in enumerate(user_defined):
            key = ud.get('key', '')
            if key.startswith('Organization'):
                issues['userdefined_org'].append({
                    'index': i,
                    'key': key,
                    'value': ud.get('value', '')
                })
        
        # Determine if contact needs update
        issues['needs_update'] = bool(
            issues['url_fixes'] or 
            issues['duplicate_linkedin'] or 
            issues['userdefined_org']
        )
        
        return issues
    
    def build_update_body(self, contact, issues):
        """Build the update body for the People API."""
        update_fields = []
        body = {}
        
        # Include etag for update
        etag = contact.get('etag')
        if etag:
            body['etag'] = etag
        
        # 1. Fix URL types
        if issues['url_fixes']:
            urls = contact.get('urls', []).copy()
            for fix in issues['url_fixes']:
                urls[fix['index']]['type'] = fix['proper_type']
            body['urls'] = urls
            update_fields.append('urls')
        
        # 2. Remove duplicate LinkedIn URLs
        if issues['duplicate_linkedin']:
            urls = body.get('urls', contact.get('urls', []).copy())
            # Remove duplicates (in reverse order to maintain indices)
            for dup in sorted(issues['duplicate_linkedin'], key=lambda x: x['index'], reverse=True):
                if dup['index'] < len(urls):
                    urls.pop(dup['index'])
            body['urls'] = urls
            if 'urls' not in update_fields:
                update_fields.append('urls')
        
        # 3. Remove userDefined organization fields
        if issues['userdefined_org']:
            user_defined = contact.get('userDefined', []).copy()
            # Remove organization fields (in reverse order)
            for ud in sorted(issues['userdefined_org'], key=lambda x: x['index'], reverse=True):
                if ud['index'] < len(user_defined):
                    user_defined.pop(ud['index'])
            
            if user_defined:
                body['userDefined'] = user_defined
                update_fields.append('userDefined')
            else:
                # If no userDefined fields left, we need to clear them
                body['userDefined'] = []
                update_fields.append('userDefined')
        
        return body, ','.join(update_fields)
    
    def update_contact(self, resource_name, body, update_fields):
        """Update a contact via the People API."""
        try:
            self.service.people().updateContact(
                resourceName=resource_name,
                updatePersonFields=update_fields,
                body=body
            ).execute()
            return True
        except HttpError as e:
            error_msg = f"Failed to update {resource_name}: {e}"
            self.stats['errors'].append(error_msg)
            print(f"ERROR: {error_msg}")
            return False
        except Exception as e:
            error_msg = f"Unexpected error updating {resource_name}: {e}"
            self.stats['errors'].append(error_msg)
            print(f"ERROR: {error_msg}")
            return False
    
    def run_cleanup(self, test_mode=False, test_count=5):
        """Run the cleanup process."""
        print("=" * 60)
        print("GOOGLE CONTACTS FIELD MAPPING CLEANUP")
        print("=" * 60)
        
        # 1. Fetch all contacts
        print("\n1. Fetching contacts...")
        contacts = self.get_all_contacts()
        print(f"   Found {len(contacts)} contacts")
        
        # 2. Create backup
        print("\n2. Creating backup...")
        backup_file = self.backup_contacts(contacts)
        
        # 3. Analyze contacts
        print("\n3. Analyzing contacts for issues...")
        contacts_to_update = []
        
        for contact in contacts:
            issues = self.analyze_contact(contact)
            if issues['needs_update']:
                contacts_to_update.append({
                    'contact': contact,
                    'issues': issues
                })
                
                # Update stats
                if issues['url_fixes']:
                    self.stats['contacts_with_url_issues'] += 1
                    self.stats['urls_fixed'] += len(issues['url_fixes'])
                if issues['duplicate_linkedin']:
                    self.stats['contacts_with_duplicate_linkedin'] += 1
                    self.stats['duplicate_linkedin_removed'] += len(issues['duplicate_linkedin'])
                if issues['userdefined_org']:
                    self.stats['contacts_with_userdefined_org'] += 1
                    self.stats['userdefined_org_removed'] += len(issues['userdefined_org'])
        
        print(f"   Contacts needing updates: {len(contacts_to_update)}")
        print(f"   - URL issues: {self.stats['contacts_with_url_issues']} contacts, {self.stats['urls_fixed']} URLs")
        print(f"   - Duplicate LinkedIn: {self.stats['contacts_with_duplicate_linkedin']} contacts, {self.stats['duplicate_linkedin_removed']} duplicates")
        print(f"   - UserDefined org fields: {self.stats['contacts_with_userdefined_org']} contacts, {self.stats['userdefined_org_removed']} fields")
        
        # 4. Test mode - only process first few contacts
        if test_mode:
            print(f"\n4. TEST MODE - Processing first {test_count} contacts...")
            contacts_to_update = contacts_to_update[:test_count]
        else:
            print(f"\n4. Processing {len(contacts_to_update)} contacts...")
        
        # 5. Update contacts
        updated_count = 0
        for i, item in enumerate(contacts_to_update):
            contact = item['contact']
            issues = item['issues']
            resource_name = contact.get('resourceName', '')
            name = (contact.get('names') or [{}])[0].get('displayName', 'Unknown')
            
            print(f"   [{i+1}/{len(contacts_to_update)}] Updating: {name}")
            
            # Build update body
            body, update_fields = self.build_update_body(contact, issues)
            
            if not update_fields:
                print(f"      No fields to update")
                continue
            
            # Update contact
            success = self.update_contact(resource_name, body, update_fields)
            if success:
                updated_count += 1
                print(f"      Updated fields: {update_fields}")
                
                # Show what was fixed
                if issues['url_fixes']:
                    for fix in issues['url_fixes']:
                        print(f"        - URL type: {fix['current_type']} -> {fix['proper_type']}")
                if issues['duplicate_linkedin']:
                    print(f"        - Removed {len(issues['duplicate_linkedin'])} duplicate LinkedIn URLs")
                if issues['userdefined_org']:
                    print(f"        - Removed {len(issues['userdefined_org'])} userDefined org fields")
            else:
                print(f"      FAILED to update")
        
        # 6. Print summary
        print("\n" + "=" * 60)
        print("CLEANUP SUMMARY")
        print("=" * 60)
        print(f"Total contacts processed: {self.stats['total_contacts']}")
        print(f"Contacts updated: {updated_count}")
        print(f"URLs fixed: {self.stats['urls_fixed']}")
        print(f"Duplicate LinkedIn URLs removed: {self.stats['duplicate_linkedin_removed']}")
        print(f"UserDefined org fields removed: {self.stats['userdefined_org_removed']}")
        print(f"Errors: {len(self.stats['errors'])}")
        
        if self.stats['errors']:
            print("\nErrors encountered:")
            for error in self.stats['errors'][:10]:  # Show first 10 errors
                print(f"  - {error}")
        
        # 7. Save stats
        stats_file = self.backup_dir / f"cleanup_stats_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(stats_file, 'w') as f:
            json.dump(self.stats, f, indent=2)
        print(f"\nStats saved to: {stats_file}")
        
        return self.stats

def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Google Contacts Field Mapping Cleanup')
    parser.add_argument('--test', action='store_true', help='Run in test mode (first 5 contacts)')
    parser.add_argument('--test-count', type=int, default=5, help='Number of contacts to test')
    parser.add_argument('--backup-dir', default=None,
                       help='Backup directory (default: $AGENT_ROOT/backups/google_contacts)')
    
    args = parser.parse_args()
    
    # Run cleanup
    cleanup = GoogleContactsCleanup(backup_dir=args.backup_dir)
    stats = cleanup.run_cleanup(test_mode=args.test, test_count=args.test_count)
    
    # Exit with error code if there were errors
    if stats['errors']:
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == '__main__':
    main()