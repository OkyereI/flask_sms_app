from flask import Flask, render_template, request, redirect, url_for, flash, session, make_response, send_from_directory, send_file, abort, jsonify
import pandas as pd
import requests
import io
import urllib.parse # Import urllib.parse for URL encoding
from werkzeug.security import generate_password_hash, check_password_hash # Import security utilities
from datetime import datetime, timedelta # Import datetime for PDF timestamp
import os # Import os for file path operations
from werkzeug.utils import secure_filename # Import secure_filename for safe file uploads
import gspread # Import gspread for Google Sheets write operations
import gspread.utils # Import for gspread utilities
from oauth2client.service_account import ServiceAccountCredentials # Import for authentication
import uuid # Import for generating unique share tokens
import hashlib # Import for secure token generation
import json
# --- HELPER FUNCTION FOR FINANCE ACCESS ---
def check_finance_access():
    """
    Check if user has admin OR finance officer access.
    Returns True if user is logged in as admin or finance officer.
    This allows finance officers to access all finance-related routes.
    """
    return session.get('admin_logged_in') or session.get('finance_logged_in')

# Excel-based database - no SQLAlchemy needed

app = Flask(__name__)

# =============================================================================
# DATABASE CONFIGURATION - EXCEL-BASED (No PostgreSQL)
# =============================================================================
# Configuration is done below with init_excel_db()
# No SQLAlchemy needed - using Excel files instead
# =============================================================================

# Check if WeasyPrint is available for PDF generation
try:
    from weasyprint import HTML
    WEASYPRINT_AVAILABLE = True
except ImportError:
    WEASYPRINT_AVAILABLE = False

# Context processor to make WEASYPRINT_AVAILABLE available in all templates
@app.context_processor
def inject_weasyprint_status():
    return dict(weasyprint_available=WEASYPRINT_AVAILABLE)

# Context processor to make datetime available in all templates
@app.context_processor
def inject_datetime():
    return dict(datetime=datetime)

# Context processor to add current datetime to all templates
@app.context_processor
def inject_now():
    return dict(now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

# Context processor to make Excel database variables available in templates
@app.context_processor
def inject_excel_config():
    # Create a function to get Excel file status
    def get_excel_status(data_type):
        """Get status and record count for an Excel file"""
        try:
            excel_path = get_excel_path(data_type)
            if os.path.exists(excel_path):
                df = pd.read_excel(excel_path)
                return {'exists': True, 'records': len(df)}
            else:
                return {'exists': False, 'records': 0}
        except:
            return {'exists': False, 'records': 0}
    
    return dict(EXCEL_DB_DIR=EXCEL_DB_DIR, EXCEL_FILES=EXCEL_FILES, get_excel_status=get_excel_status)

# --- Configuration ---
# IMPORTANT: For Render deployment, use 'google_sheet' mode
# The database uses SQLite by default which works on Render's filesystem
# For production on Render, consider using PostgreSQL add-on
DATA_SOURCE = 'google_sheet'  # Changed from 'local_excel' to use Google Sheets

# =============================================================================
# GOOGLE SHEET SETUP INSTRUCTIONS
# =============================================================================
# To make your Google Sheet accessible:
# 
# 1. Open your Google Sheet (containing student results)
# 2. Click "File" → "Share" → "Publish to web"
# 3. Select "Entire document" and "Comma-separated values (.csv)"
# 4. Click "Publish" and copy the generated link
# 5. Paste it in the GOOGLE_SHEET_CSV_URL below
#
# Example CSV URL format:
# https://docs.google.com/spreadsheets/d/e/2PACX-1v.../pub?output=csv
#
# IMPORTANT: The sheet must be published to web for anyone to read it
# =============================================================================

# For Google Sheets - Use this mode for both local and Render deployment
# IMPORTANT: This is your PRIMARY external database - students will be loaded from here first
# Sheet: https://docs.google.com/spreadsheets/d/1JYs4ZtUKfklu-bEqdYOeeKu6nF7rM5I55EQZb-yrs-A/edit?gid=0#gid=0
GOOGLE_SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vR6QYC30lQNjpJjHPJFUG6XUUZqP5XfnNjBUB4Xrhb7pzFP87-IF_2_iRAdJKCUk5zJThu-ml1hzyFm/pub?output=csv"

# Store Inventory Google Sheet CSV URL (Publish to web from your store inventory sheet)
# Replace with your store inventory Google Sheet CSV URL
STORE_INVENTORY_SHEET_URL = "https://docs.google.com/spreadsheets/d/1JYs4ZtUKfklu-bEqdYOeeKu6nF7rM5I55EQZb-yrs-A/edit?gid=0#gid=0"

# Alternative: Using the sheet ID (if you prefer gviz format)
GOOGLE_SHEET_ID = "1JYs4ZtUKfklu-bEqdYOeeKu6nF7rM5I55EQZb-yrs-A"  # Your Google Sheet ID
GOOGLE_SHEET_NAME = "Sheet1"  # Sheet name in your Google Sheet

# UNIFIED Google Sheet Configuration for ALL databases
# This single Google Sheet will contain multiple worksheets (one per data type)
# Get the ID from your Google Sheet URL: https://docs.google.com/spreadsheets/d/SHEET_ID/edit
# Your actual Google Sheet Document ID: 1JYs4ZtUKfklu-bEqdYOeeKu6nF7rM5I55EQZb-yrs-A
UNIFIED_GOOGLE_SHEET_ID = "1JYs4ZtUKfklu-bEqdYOeeKu6nF7rM5I55EQZb-yrs-A"
UNIFIED_SHEET_ENABLED = True  # Set to False to disable unified sheet sync

# Map data types to worksheet names in the unified Google Sheet
SHEET_WORKBOOKS = {
    'students': 'Student',
    'payments': 'Payments',
    'store_items': 'Store Items',
    'store_transactions': 'Store Transactions',
    'assets': 'Assets',
    'expenses': 'Expenses',
    'suppliers': 'Suppliers',
    'fee_types': 'Fee Types',
    'locations': 'Locations',
    'student_accounts': 'Student Accounts',
    'asset_movements': 'Asset Movements',
    'maintenance_requests': 'Maintenance Requests',
    'instructors': 'Instructors',
    'forms': 'Forms',
    'result_submissions': 'Result Submissions',
    'settings': 'Settings',
    'staff_accounts': 'Staff Accounts'
}

# Store Items Google Sheet Configuration (for direct API push)
STORE_ITEMS_SHEET_ID = "1JYs4ZtUKfklu-bEqdYOeeKu6nF7rM5I55EQZb-yrs-A"
STORE_ITEMS_SHEET_NAME = "Store Items"

# =============================================================================
# DATABASE CONFIGURATION - EXCEL-BASED (No PostgreSQL)
# =============================================================================
# This app now uses Excel files for data storage
# - Offline mode: Data stored in local Excel files
# - Online mode: Sync data from Google Sheets to Excel
# =============================================================================

app.config['SECRET_KEY'] = 'your_very_secret_key_replace_this'

# Excel Database Directory
EXCEL_DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'excel_db')
if not os.path.exists(EXCEL_DB_DIR):
    os.makedirs(EXCEL_DB_DIR)
    print(f"Created Excel database directory: {EXCEL_DB_DIR}")

# Excel file names for each data type
EXCEL_FILES = {
    'students': 'students.xlsx',
    'payments': 'payments.xlsx',
    'store_items': 'store_items.xlsx',
    'store_transactions': 'store_transactions.xlsx',
    'assets': 'assets.xlsx',
    'expenses': 'expenses.xlsx',
    'suppliers': 'suppliers.xlsx',
    'fee_types': 'fee_types.xlsx',
    'locations': 'locations.xlsx',
    'student_accounts': 'student_accounts.xlsx',
    'asset_movements': 'asset_movements.xlsx',
    'maintenance_requests': 'maintenance_requests.xlsx',
    'instructors': 'instructors.xlsx',
    'forms': 'forms.xlsx',
    'result_submissions': 'result_submissions.xlsx',
    'settings': 'settings.xlsx',
    'staff_accounts': 'staff_users.xlsx'
}


# Function to get Excel file path
def get_excel_path(data_type):
    """Get the full path for an Excel file"""
    if data_type not in EXCEL_FILES:
        raise ValueError(f"Unknown data type: {data_type}")
    return os.path.join(EXCEL_DB_DIR, EXCEL_FILES[data_type])

# Initialize Excel files with headers if they don't exist
def init_excel_db():
    """Initialize Excel database files with proper headers"""
    # Students - Updated with exam results fields and Form column
    if not os.path.exists(get_excel_path('students')):
        df = pd.DataFrame(columns=[
            'id', 'student_id', 'student_name', 'Form', 'department', 'parent_phone',
            # Math scores
            'math_exams_score_2021_1', 'math_class_score_2021_1', 'math_total_score_2021_1', 'math_remarks_2021_1', 'math_grade_2021_1',
            # Science scores
            'science_exams_score_2021_1', 'science_class_score_2021_1', 'science_total_score_2021_1', 'science_remarks_2021_1', 'science_grade_2021_1',
            # Social scores
            'social_exams_score_2021_1', 'social_class_score_2021_1', 'social_total_score_2021_1', 'social_remarks_2021_1', 'social_grade_2021_1',
            'created_at'
        ])
        df.to_excel(get_excel_path('students'), index=False)
    else:
        # Add 'Form' column if it doesn't exist in existing file
        try:
            df_existing = pd.read_excel(get_excel_path('students'))
            if 'Form' not in df_existing.columns:
                df_existing['Form'] = ''  # Add empty Form column for existing students
                df_existing.to_excel(get_excel_path('students'), index=False)
                print("Added 'Form' column to existing students.xlsx")
        except Exception as e:
            print(f"Error updating students.xlsx: {e}")
    
    # Payments
    if not os.path.exists(get_excel_path('payments')):
        df = pd.DataFrame(columns=['id', 'student_id', 'student_name', 'fee_type', 'amount', 'payment_date', 'payment_method', 'receipt_number', 'created_at'])
        df.to_excel(get_excel_path('payments'), index=False)
    
    # Store Items
    if not os.path.exists(get_excel_path('store_items')):
        df = pd.DataFrame(columns=['id', 'name', 'category', 'quantity', 'unit_price', 'min_threshold', 'created_at'])
        df.to_excel(get_excel_path('store_items'), index=False)
    
    # Store Transactions
    if not os.path.exists(get_excel_path('store_transactions')):
        df = pd.DataFrame(columns=['id', 'item_id', 'transaction_type', 'quantity', 'recipient', 'recipient_type', 'issued_by', 'notes', 'created_at'])
        df.to_excel(get_excel_path('store_transactions'), index=False)
    
    # Assets
    if not os.path.exists(get_excel_path('assets')):
        df = pd.DataFrame(columns=['id', 'asset_code', 'name', 'description', 'category', 'status', 'location_id', 'purchase_date', 'purchase_price', 'created_at'])
        df.to_excel(get_excel_path('assets'), index=False)
    
    # Expenses
    if not os.path.exists(get_excel_path('expenses')):
        df = pd.DataFrame(columns=['id', 'description', 'amount', 'category', 'date', 'recorded_by', 'created_at'])
        df.to_excel(get_excel_path('expenses'), index=False)
    
    # Suppliers
    if not os.path.exists(get_excel_path('suppliers')):
        df = pd.DataFrame(columns=['id', 'name', 'contact_person', 'phone', 'email', 'address', 'created_at'])
        df.to_excel(get_excel_path('suppliers'), index=False)
    
    # Fee Types
    if not os.path.exists(get_excel_path('fee_types')):
        df = pd.DataFrame(columns=['id', 'name', 'amount', 'academic_year', 'is_active', 'created_at'])
        df.to_excel(get_excel_path('fee_types'), index=False)
    
    # Locations
    if not os.path.exists(get_excel_path('locations')):
        df = pd.DataFrame(columns=['id', 'name', 'description', 'created_at'])
        df.to_excel(get_excel_path('locations'), index=False)
    
    # Student Accounts
    if not os.path.exists(get_excel_path('student_accounts')):
        df = pd.DataFrame(columns=['id', 'student_id', 'total_debit', 'total_credit', 'balance', 'updated_at'])
        df.to_excel(get_excel_path('student_accounts'), index=False)
    
    # Asset Movements
    if not os.path.exists(get_excel_path('asset_movements')):
        df = pd.DataFrame(columns=['id', 'asset_id', 'from_location_id', 'to_location_id', 'moved_by', 'notes', 'created_at'])
        df.to_excel(get_excel_path('asset_movements'), index=False)
    
    # Maintenance Requests
    if not os.path.exists(get_excel_path('maintenance_requests')):
        df = pd.DataFrame(columns=['id', 'asset_id', 'location_id', 'issue_description', 'priority', 'status', 'estimated_cost', 'actual_cost', 'reported_by', 'contractor', 'completed_date', 'notes', 'created_at'])
        df.to_excel(get_excel_path('maintenance_requests'), index=False)
    
    # Instructors
    if not os.path.exists(get_excel_path('instructors')):
        df = pd.DataFrame(columns=['id', 'instructor_id', 'name', 'username', 'password_hash', 'assigned_subjects', 'assigned_forms', 'created_at'])
        df.to_excel(get_excel_path('instructors'), index=False)
    
    # Forms (NEW)
    if not os.path.exists(get_excel_path('forms')):
        df = pd.DataFrame(columns=['FormID', 'FormName', 'Level', 'created_at'])
        df.to_excel(get_excel_path('forms'), index=False)
        print(f"Created forms.xlsx")

    
    # Result Submissions tracking (NEW)
    if not os.path.exists(get_excel_path('result_submissions')):
        df = pd.DataFrame(columns=[
            'id', 'instructor_id', 'instructor_name', 'student_id', 
            'subject', 'semester', 'year', 'score', 'grade', 
            'remarks', 'status', 'submitted_at'
        ])
        df.to_excel(get_excel_path('result_submissions'), index=False)
        print(f"Created result_submissions.xlsx")

    # Settings (NEW)
    if not os.path.exists(get_excel_path('settings')):
        df = pd.DataFrame(columns=['key', 'value', 'updated_at'])
        df.to_excel(get_excel_path('settings'), index=False)
        print(f"Created settings.xlsx")



# Initialize the database
init_excel_db()

print("\n" + "="*60)
print("APP CONFIGURATION:")
print("="*60)
print("Database: Excel files (local)")
print("Online Sync: Google Sheets available")
print(f"Data Directory: {EXCEL_DB_DIR}")
print("="*60 + "\n")

# =============================================================================
# EXCEL DATABASE HELPER FUNCTIONS (Mimics SQLAlchemy interface)
# =============================================================================

class ExcelModel:
    """Base class for Excel-based models"""
    
    @staticmethod
    def get_all(data_type):
        """Get all records from Excel file"""
        try:
            df = pd.read_excel(get_excel_path(data_type))
            return df.to_dict('records')
        except Exception as e:
            print(f"Error getting all {data_type}: {e}")
            return []
    
    @staticmethod
    def get_by_id(data_type, record_id):
        """Get a single record by ID"""
        try:
            df = pd.read_excel(get_excel_path(data_type))
            # Handle type mismatch: form sends string, Excel has int/float
            try:
                record_id_num = int(record_id)
            except (ValueError, TypeError):
                record_id_num = record_id
            # Try both string and numeric comparison
            if 'id' in df.columns:
                # Create mask for both string and int/float comparison
                mask = (df['id'].astype(str) == str(record_id)) | (df['id'] == record_id_num)
                record = df[mask]
            else:
                record = df[df.index == record_id]
            if not record.empty:
                return record.iloc[0].to_dict()
            return None
        except Exception as e:
            print(f"Error getting {data_type} by id: {e}")
            return None
    
    @staticmethod
    def get_one_by(data_type, **kwargs):
        """Get a single record by any field"""
        try:
            df = pd.read_excel(get_excel_path(data_type))
            for key, value in kwargs.items():
                if key in df.columns:
                    # Handle type mismatch: convert both to string for comparison
                    df = df[df[key].astype(str) == str(value)]
            if not df.empty:
                return df.iloc[0].to_dict()
            return None
        except Exception as e:
            print(f"Error getting {data_type}: {e}")
            return None
    
    @staticmethod
    def filter_by(data_type, **kwargs):
        """Filter records by fields"""
        try:
            df = pd.read_excel(get_excel_path(data_type))
            for key, value in kwargs.items():
                if key in df.columns:
                    # Handle type mismatch: convert both to string for comparison
                    df = df[df[key].astype(str) == str(value)]
            return df.to_dict('records')
        except Exception as e:
            print(f"Error filtering {data_type}: {e}")
            return []
    
    @staticmethod
    def count(data_type):
        """Count total records"""
        try:
            df = pd.read_excel(get_excel_path(data_type))
            return len(df)
        except:
            return 0
    
    @staticmethod
    def add(data_type, **kwargs):
        """Add a new record"""
        try:
            df = pd.read_excel(get_excel_path(data_type))
            new_id = 1 if df.empty else df['id'].max() + 1
            kwargs['id'] = new_id
            if 'created_at' not in kwargs:
                kwargs['created_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            new_row = pd.DataFrame([kwargs])
            df = pd.concat([df, new_row], ignore_index=True)
            df.to_excel(get_excel_path(data_type), index=False)
            return new_id
        except Exception as e:
            print(f"Error adding {data_type}: {e}")
            return None
    
    @staticmethod
    def update(data_type, record_id, **kwargs):
        """Update a record"""
        try:
            df = pd.read_excel(get_excel_path(data_type))
            # Handle type mismatch: form sends string, Excel has int/float
            try:
                record_id_num = int(record_id)
            except (ValueError, TypeError):
                record_id_num = record_id
            # Try both string and numeric comparison
            if 'id' in df.columns:
                mask = (df['id'].astype(str) == str(record_id)) | (df['id'] == record_id_num)
                idx = df[mask].index
            else:
                idx = df[df.index == record_id].index
            if len(idx) > 0:
                for key, value in kwargs.items():
                    if key in df.columns:
                        df.loc[idx[0], key] = value
                df.to_excel(get_excel_path(data_type), index=False)
                return True
            return False
        except Exception as e:
            print(f"Error updating {data_type}: {e}")
            return False
    
    @staticmethod
    def delete(data_type, record_id):
        """Delete a record"""
        try:
            df = pd.read_excel(get_excel_path(data_type))
            # Handle type mismatch: form sends string, Excel has int/float
            try:
                record_id_num = int(record_id)
            except (ValueError, TypeError):
                record_id_num = record_id
            # Try both string and numeric comparison - keep rows that don't match
            if 'id' in df.columns:
                mask = (df['id'].astype(str) != str(record_id)) & (df['id'] != record_id_num)
                df = df[mask]
            else:
                df = df[df.index != record_id]
            df.to_excel(get_excel_path(data_type), index=False)
            return True
        except Exception as e:
            print(f"Error deleting {data_type}: {e}")
            return False

# =============================================================================
# HELPER FUNCTIONS for Excel Data Operations
# =============================================================================
def load_excel_data(data_type):
    """Load data from Excel file and return DataFrame"""
    try:
        excel_path = get_excel_path(data_type)
        if os.path.exists(excel_path):
            df = pd.read_excel(excel_path)
            return df
        return pd.DataFrame()
    except Exception as e:
        print(f"Error loading {data_type}: {e}")
        return pd.DataFrame()

def save_excel_data(data_type, df):
    """Save DataFrame to Excel file"""
    try:
        excel_path = get_excel_path(data_type)
        df.to_excel(excel_path, index=False)
        return True
    except Exception as e:
        print(f"Error saving {data_type}: {e}")
        return False

# Create model classes for each data type
class SchoolStoreItem:
    """Excel-based Store Item model with Google Sheets sync - FIXED VERSION"""
    data_type = 'store_items'
    
    @classmethod
    def _sync_from_google_sheet(cls):
        """Sync store items from Google Sheet to local Excel - FIXED VERSION"""
        if not STORE_INVENTORY_SHEET_URL:
            print("No STORE_INVENTORY_SHEET_URL configured")
            return None
        try:
            # Read from Google Sheet CSV
            df = pd.read_csv(STORE_INVENTORY_SHEET_URL)
            if df.empty:
                print("Google Sheet is empty")
                return None
            
            print(f"Loaded {len(df)} rows from Google Sheet")
            print(f"Original columns: {df.columns.tolist()}")
            
            # Create a mapping of common column name variations
            column_mapping = {}
            for col in df.columns:
                col_lower = str(col).lower().strip()
                if 'name' in col_lower or 'item' in col_lower:
                    column_mapping[col] = 'name'
                elif 'category' in col_lower or 'type' in col_lower:
                    column_mapping[col] = 'category'
                elif 'unit' in col_lower or 'measurement' in col_lower or 'uom' in col_lower:
                    column_mapping[col] = 'unit'
                elif 'quantity' in col_lower or 'qty' in col_lower or 'stock' in col_lower:
                    column_mapping[col] = 'quantity'
                elif 'min' in col_lower or 'threshold' in col_lower or 'reorder' in col_lower:
                    column_mapping[col] = 'min_threshold'
                elif 'price' in col_lower or 'cost' in col_lower or 'rate' in col_lower:
                    column_mapping[col] = 'unit_price'
                elif 'id' in col_lower:
                    column_mapping[col] = 'id'
            
            print(f"Column mapping: {column_mapping}")
            
            # Rename columns to match our schema
            df = df.rename(columns=column_mapping)
            
            # Ensure required columns exist with correct defaults
            if 'name' not in df.columns:
                df['name'] = ''
            if 'category' not in df.columns:
                df['category'] = 'General'
            if 'unit' not in df.columns:
                df['unit'] = 'pcs'
            if 'quantity' not in df.columns:
                df['quantity'] = 0
            if 'min_threshold' not in df.columns:
                df['min_threshold'] = 0
            if 'unit_price' not in df.columns:
                df['unit_price'] = 0
            if 'id' not in df.columns:
                df['id'] = ''
            
            # Convert numeric columns properly
            try:
                df['quantity'] = pd.to_numeric(df['quantity'], errors='coerce').fillna(0)
            except:
                df['quantity'] = 0
                
            try:
                df['min_threshold'] = pd.to_numeric(df['min_threshold'], errors='coerce').fillna(0)
            except:
                df['min_threshold'] = 0
                
            try:
                df['unit_price'] = pd.to_numeric(df['unit_price'], errors='coerce').fillna(0)
            except:
                df['unit_price'] = 0
            
            # Select only the columns we need
            final_columns = ['id', 'name', 'category', 'unit', 'quantity', 'min_threshold', 'unit_price']
            df_final = df[final_columns].copy()
            
            # Add created_at timestamp if not present
            if 'created_at' not in df_final.columns:
                df_final['created_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # Save to local Excel
            excel_path = get_excel_path(cls.data_type)
            df_final.to_excel(excel_path, index=False)
            print(f"Synced {len(df_final)} store items to local Excel: {excel_path}")
            
            return df_final
            
        except Exception as e:
            print(f"Error syncing store items from Google Sheet: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    @classmethod
    def query(cls):
        return cls
    
    @classmethod
    def all(cls):
        """Get all store items - first try local Excel, then Google Sheets"""
        # Try local Excel first
        try:
            excel_path = get_excel_path(cls.data_type)
            if os.path.exists(excel_path):
                df = pd.read_excel(excel_path)
                if not df.empty and len(df) > 0:
                    # Filter out rows where 'id' is NaN or name is empty
                    df = df[df['id'].notna() & df['name'].notna() & (df['name'].astype(str).str.strip() != '')]
                    if len(df) > 0:
                        # Clean up any timestamp values in unit/threshold columns
                        df = cls._clean_data(df)
                        return df.to_dict('records')
        except Exception as e:
            print(f"Error reading store items from Excel: {e}")
        
        # Try Google Sheets if local is empty
        synced_df = cls._sync_from_google_sheet()
        if synced_df is not None and not synced_df.empty:
            return synced_df.to_dict('records')
        
        return []
    
    @classmethod
    def _clean_data(cls, df):
        """Clean up corrupted data - remove timestamps from unit/threshold fields"""
        try:
            # If unit column contains timestamps, it's corrupted
            if 'unit' in df.columns:
                # Check if any unit value looks like a timestamp
                def is_timestamp(val):
                    val_str = str(val)
                    # Timestamps typically look like: 2026-05-31 05:07:56
                    return ('-' in val_str and ':' in val_str) or ('-' in val_str and len(val_str) > 10)
                
                # If corrupted, set to default 'pcs'
                df.loc[df['unit'].apply(is_timestamp), 'unit'] = 'pcs'
            
            # Clean min_threshold - should be numeric
            if 'min_threshold' in df.columns:
                # Extract just the numeric part if there's a timestamp attached
                def extract_numeric(val):
                    val_str = str(val)
                    if is_timestamp(val_str):
                        return 0  # Default if corrupted
                    try:
                        return float(val_str.split()[0]) if ' ' in val_str else float(val_str)
                    except:
                        return 0
                
                df['min_threshold'] = df['min_threshold'].apply(extract_numeric)
            
            return df
            
        except Exception as e:
            print(f"Error cleaning data: {e}")
            return df
    
    @classmethod
    def get_by_id(cls, id):
        return ExcelModel.get_by_id(cls.data_type, id)
    
    @classmethod
    def get_one_by(cls, **kwargs):
        return ExcelModel.get_one_by(cls.data_type, **kwargs)
    
    @classmethod
    def filter_by(cls, **kwargs):
        return ExcelModel.filter_by(cls.data_type, **kwargs)
    
    @classmethod
    def count(cls):
        """Count total store items"""
        items = cls.all()
        return len(items)
    
    @classmethod
    def add(cls, **kwargs):
        return ExcelModel.add(cls.data_type, **kwargs)
    
    @classmethod
    def update(cls, id, **kwargs):
        return ExcelModel.update(cls.data_type, id, **kwargs)
    
    @classmethod
    def delete(cls, id):
        return ExcelModel.delete(cls.data_type, id)

class SchoolStoreTransaction:
    """Excel-based Store Transaction model"""
    data_type = 'store_transactions'
    
    @classmethod
    def query(cls):
        return cls
    
    @classmethod
    def all(cls):
        return ExcelModel.get_all(cls.data_type)
    
    @classmethod
    def get_by_id(cls, id):
        return ExcelModel.get_by_id(cls.data_type, id)
    
    @classmethod
    def filter_by(cls, **kwargs):
        return ExcelModel.filter_by(cls.data_type, **kwargs)
    
    @classmethod
    def count(cls):
        return ExcelModel.count(cls.data_type)
    
    @classmethod
    def add(cls, **kwargs):
        return ExcelModel.add(cls.data_type, **kwargs)
    
    @classmethod
    def update(cls, id, **kwargs):
        return ExcelModel.update(cls.data_type, id, **kwargs)

class SchoolPayment:
    """Excel-based Payment model"""
    data_type = 'payments'
    
    @classmethod
    def query(cls):
        return cls
    
    @classmethod
    def all(cls):
        return ExcelModel.get_all(cls.data_type)
    
    @classmethod
    def get_by_id(cls, id):
        return ExcelModel.get_by_id(cls.data_type, id)
    
    @classmethod
    def filter_by(cls, **kwargs):
        return ExcelModel.filter_by(cls.data_type, **kwargs)
    
    @classmethod
    def count(cls):
        return ExcelModel.count(cls.data_type)
    
    @classmethod
    def add(cls, **kwargs):
        return ExcelModel.add(cls.data_type, **kwargs)
    
    @classmethod
    def update(cls, id, **kwargs):
        return ExcelModel.update(cls.data_type, id, **kwargs)

class SchoolFeeType:
    """Excel-based Fee Type model"""
    data_type = 'fee_types'
    
    @classmethod
    def query(cls):
        return cls
    
    @classmethod
    def all(cls):
        return ExcelModel.get_all(cls.data_type)
    
    @classmethod
    def get_by_id(cls, id):
        return ExcelModel.get_by_id(cls.data_type, id)
    
    @classmethod
    def filter_by(cls, **kwargs):
        return ExcelModel.filter_by(cls.data_type, **kwargs)
    
    @classmethod
    def count(cls):
        return ExcelModel.count(cls.data_type)
    
    @classmethod
    def add(cls, **kwargs):
        return ExcelModel.add(cls.data_type, **kwargs)
    
    @classmethod
    def update(cls, id, **kwargs):
        return ExcelModel.update(cls.data_type, id, **kwargs)

class SchoolAsset:
    """Excel-based Asset model"""
    data_type = 'assets'
    
    @classmethod
    def query(cls):
        return cls
    
    @classmethod
    def all(cls):
        return ExcelModel.get_all(cls.data_type)
    
    @classmethod
    def get_by_id(cls, id):
        return ExcelModel.get_by_id(cls.data_type, id)
    
    @classmethod
    def filter_by(cls, **kwargs):
        return ExcelModel.filter_by(cls.data_type, **kwargs)
    
    @classmethod
    def count(cls):
        return ExcelModel.count(cls.data_type)
    
    @classmethod
    def add(cls, **kwargs):
        return ExcelModel.add(cls.data_type, **kwargs)
    
    @classmethod
    def update(cls, id, **kwargs):
        return ExcelModel.update(cls.data_type, id, **kwargs)

class SchoolLocation:
    """Excel-based Location model"""
    data_type = 'locations'
    
    @classmethod
    def query(cls):
        return cls
    
    @classmethod
    def all(cls):
        return ExcelModel.get_all(cls.data_type)
    
    @classmethod
    def get_by_id(cls, id):
        return ExcelModel.get_by_id(cls.data_type, id)
    
    @classmethod
    def filter_by(cls, **kwargs):
        return ExcelModel.filter_by(cls.data_type, **kwargs)
    
    @classmethod
    def count(cls):
        return ExcelModel.count(cls.data_type)
    
    @classmethod
    def add(cls, **kwargs):
        return ExcelModel.add(cls.data_type, **kwargs)

class SchoolExpense:
    """Excel-based Expense model"""
    data_type = 'expenses'
    
    @classmethod
    def query(cls):
        return cls
    
    @classmethod
    def all(cls):
        return ExcelModel.get_all(cls.data_type)
    
    @classmethod
    def get_by_id(cls, id):
        return ExcelModel.get_by_id(cls.data_type, id)
    
    @classmethod
    def filter_by(cls, **kwargs):
        return ExcelModel.filter_by(cls.data_type, **kwargs)
    
    @classmethod
    def count(cls):
        return ExcelModel.count(cls.data_type)
    
    @classmethod
    def add(cls, **kwargs):
        return ExcelModel.add(cls.data_type, **kwargs)

class SchoolSupplier:
    """Excel-based Supplier model"""
    data_type = 'suppliers'
    
    @classmethod
    def query(cls):
        return cls
    
    @classmethod
    def all(cls):
        return ExcelModel.get_all(cls.data_type)
    
    @classmethod
    def get_by_id(cls, id):
        return ExcelModel.get_by_id(cls.data_type, id)
    
    @classmethod
    def filter_by(cls, **kwargs):
        return ExcelModel.filter_by(cls.data_type, **kwargs)
    
    @classmethod
    def count(cls):
        return ExcelModel.count(cls.data_type)
    
    @classmethod
    def add(cls, **kwargs):
        return ExcelModel.add(cls.data_type, **kwargs)

class SchoolStudentAccount:
    """Excel-based Student Account model"""
    data_type = 'student_accounts'
    
    @classmethod
    def query(cls):
        return cls
    
    @classmethod
    def all(cls):
        return ExcelModel.get_all(cls.data_type)
    
    @classmethod
    def get_by_id(cls, id):
        return ExcelModel.get_by_id(cls.data_type, id)
    
    @classmethod
    def filter_by(cls, **kwargs):
        return ExcelModel.filter_by(cls.data_type, **kwargs)
    
    @classmethod
    def count(cls):
        return ExcelModel.count(cls.data_type)
    
    @classmethod
    def add(cls, **kwargs):
        return ExcelModel.add(cls.data_type, **kwargs)
    
    @classmethod
    def update(cls, id, **kwargs):
        return ExcelModel.update(cls.data_type, id, **kwargs)


class SchoolStudent:
    """Excel-based Student model for managing student records"""
    data_type = 'students'
    
    @classmethod
    def query(cls):
        return cls
    
    @classmethod
    def all(cls):
        return ExcelModel.get_all(cls.data_type)
    
    @classmethod
    def get_by_id(cls, id):
        return ExcelModel.get_by_id(cls.data_type, id)
    
    @classmethod
    def get_by_student_id(cls, student_id):
        return ExcelModel.get_one_by(cls.data_type, student_id=student_id)
    
    @classmethod
    def filter_by(cls, **kwargs):
        return ExcelModel.filter_by(cls.data_type, **kwargs)
    
    @classmethod
    def count(cls):
        return ExcelModel.count(cls.data_type)
    
    @classmethod
    def add(cls, **kwargs):
        return ExcelModel.add(cls.data_type, **kwargs)
    
    @classmethod
    def update(cls, id, **kwargs):
        return ExcelModel.update(cls.data_type, id, **kwargs)
    
    @classmethod
    def delete(cls, id):
        return ExcelModel.delete(cls.data_type, id)
    
    @classmethod
    def search(cls, query):
        """Search students by name, student ID, or department"""
        all_students = cls.all()
        if not query:
            return all_students
        query_lower = query.lower()
        return [s for s in all_students 
                if query_lower in str(s.get('student_id', '')).lower() 
                or query_lower in str(s.get('student_name', '')).lower()
                or query_lower in str(s.get('department', '')).lower()
                or query_lower in str(s.get('parent_phone', '')).lower()]


class SchoolMaintenanceRequest:
    """Excel-based Maintenance Request model"""
    data_type = 'maintenance_requests'
    
    @classmethod
    def query(cls):
        return cls
    
    @classmethod
    def all(cls):
        return ExcelModel.get_all(cls.data_type)
    
    @classmethod
    def get_by_id(cls, id):
        return ExcelModel.get_by_id(cls.data_type, id)
    
    @classmethod
    def filter_by(cls, **kwargs):
        return ExcelModel.filter_by(cls.data_type, **kwargs)
    
    @classmethod
    def count(cls):
        return ExcelModel.count(cls.data_type)
    
    @classmethod
    def add(cls, **kwargs):
        return ExcelModel.add(cls.data_type, **kwargs)
    
    @classmethod
    def update(cls, id, **kwargs):
        return ExcelModel.update(cls.data_type, id, **kwargs)

# For backwards compatibility, create a db object that handles add and commit
class FakeDB:
    """Fake db object for backwards compatibility with SQLAlchemy syntax"""
    
    class Session:
        """Fake session object"""
        _pending_objects = []
        
        @staticmethod
        def add(obj):
            """Add an object to be saved"""
            FakeDB.Session._pending_objects.append(obj)
        
        @staticmethod
        def commit():
            """Save all pending objects to Excel"""
            for obj in FakeDB.Session._pending_objects:
                # Determine the model type and save accordingly
                model_name = type(obj).__name__
                
                # Map model names to their classes
                model_map = {
                    'dict': obj,  # If it's already a dict, use it directly
                }
                
                # Check if obj is a dict
                if isinstance(obj, dict):
                    # Determine data_type from the dict or use a default
                    data_type = obj.get('_data_type', 'store_items')
                    
                    # Try to determine the correct data_type based on fields
                    if 'asset_code' in obj:
                        data_type = 'assets'
                    elif 'payment_date' in obj:
                        data_type = 'payments'
                    elif 'fee_type' in obj or 'academic_year' in obj:
                        data_type = 'fee_types'
                    elif 'location_id' in obj and 'asset_id' in obj:
                        data_type = 'maintenance_requests'
                    elif 'recipient' in obj and 'transaction_type' in obj:
                        data_type = 'store_transactions'
                    elif 'balance' in obj and 'student_id' in obj:
                        data_type = 'student_accounts'
                    elif 'issue_description' in obj:
                        data_type = 'maintenance_requests'
                    elif 'quantity' in obj and 'unit_price' in obj:
                        data_type = 'store_items'
                    elif 'expense' in obj or 'category' in obj:
                        data_type = 'expenses'
                    elif 'name' in obj and 'description' in obj and 'status' not in obj:
                        data_type = 'locations'
                    elif 'name' in obj and 'contact_person' in obj:
                        data_type = 'suppliers'
                    else:
                        data_type = 'store_items'  # default
                    
                    ExcelModel.add(data_type, **obj)
            
            FakeDB.Session._pending_objects = []
        
        @staticmethod
        def rollback():
            """Clear pending objects"""
            FakeDB.Session._pending_objects = []
    
    session = Session()
    
    @staticmethod
    def create_all():
        pass
    
    @staticmethod
    def engine():
        return None

db = FakeDB()

# Arkesel API Configuration
# IMPORTANT: Double-check that this API key is correct and active in your Arkesel account.
# Using the API key provided by the user for the older endpoint.
ARKESEL_API_KEY = "b0FrYkNNVlZGSmdrendVT3hwUHk"
# Using the older GET-based SMS send URL provided by the user.
ARKESEL_SMS_URL = "https://sms.arkesel.com/sms/api"
# IMPORTANT: Replace with your registered Arkesel Sender ID.
# Verify this Sender ID is registered and approved in your Arkesel account.
ARKESEL_SENDER_ID = "GyeduTech" # e.g., "MySchool"

# --- Google Sheets API Configuration for Writing ---
# Path to your service account credentials JSON file
# You need to download this from Google Cloud Console
SERVICE_ACCOUNT_FILE = 'service_account_credentials.json'
# Define the scope for Google Sheets API
SCOPES = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']

# =============================================================================
# EXCEL DATABASE CONFIGURATION
# =============================================================================
# Option 2: Local Excel Database (ALREADY DEFINED ABOVE)
# - Data is stored in local Excel files
# - Can be synced from Google Sheets for backup
# - Files are stored in the 'excel_db' directory
# NOTE: EXCEL_FILES, EXCEL_DB_DIR, and get_excel_path are already defined above
# DO NOT REDEFINE THEM HERE to avoid overwriting the complete configuration
# =============================================================================

def handle_department_login(dept_name, template_name, session_key):
    if request.method == 'POST':
        username = request.form.get('username').strip()
        password = request.form.get('password').strip()
        file_path = get_excel_path('staff_accounts')  # Use unified path from EXCEL_DB_DIR

        if not os.path.exists(file_path):
            flash('Error: Staff database not found. Contact Admin.', 'danger')
            return render_template(template_name)

        try:
            df = pd.read_excel(file_path)
            # Find user with matching username and role
            user_row = df[(df['username'].str.strip() == username) & 
              (df['role'].str.strip().str.lower() == dept_name.lower())]
            if not user_row.empty:
                stored_hashed_pw = user_row.iloc[0]['password']
                
                if check_password_hash(stored_hashed_pw, password):
                    # Login Success
                    session[session_key] = True
                    session['staff_username'] = username
                    session['staff_role'] = dept_name
                    flash(f'Welcome to the {dept_name.capitalize()} Dashboard!', 'success')
                    
                    # Redirect to their specific dashboard (without admin_ prefix)
                    return redirect(url_for(f'{dept_name}_dashboard'))
                else:
                    flash('Invalid password.', 'danger')
            else:
                flash(f'User not found or not assigned to {dept_name}.', 'danger')

        except Exception as e:
            print(f"Login Error: {e}")
            print(f"Login Attempt - User: {username}, Dept: {dept_name}")
            print(f"Users found in Excel: {df['username'].tolist()}")
            flash('System error during login.', 'danger')

    return render_template(template_name)
# Function to sync data from Google Sheets to Excel
def sync_from_google_sheet_to_excel(sheet_url, data_type):
    """
    Fetch data from Google Sheet and save to local Excel file
    Returns: DataFrame or None if failed
    """
    try:
        # Read from Google Sheet
        df = pd.read_csv(sheet_url)
        
        # Save to Excel
        excel_path = get_excel_path(data_type)
        df.to_excel(excel_path, index=False)
        print(f"Synced {data_type} to Excel: {excel_path}")
        
        return df
    except Exception as e:
        print(f"Error syncing {data_type} from Google Sheet: {e}")
        return None

# Function to load data from Excel
def load_from_excel(data_type):
    """
    Load data from local Excel file
    Returns: DataFrame or None if file doesn't exist
    """
    try:
        excel_path = get_excel_path(data_type)
        if os.path.exists(excel_path):
            df = pd.read_excel(excel_path)
            print(f"Loaded {data_type} from Excel: {len(df)} records")
            return df
        else:
            print(f"Excel file not found: {excel_path}")
            return None
    except Exception as e:
        print(f"Error loading {data_type} from Excel: {e}")
        return None

# Function to save data to Excel
def save_to_excel(df, data_type):
    """
    Save DataFrame to local Excel file
    """
    try:
        excel_path = get_excel_path(data_type)
        df.to_excel(excel_path, index=False)
        print(f"Saved {data_type} to Excel: {excel_path}")
        return True
    except Exception as e:
        print(f"Error saving {data_type} to Excel: {e}")
        return False

def get_google_sheet_client():
    """Authenticates and returns a Google Sheets client."""
    try:
        import base64
        import json
        
        creds_b64 = os.getenv('GOOGLE_CREDENTIALS')
        if creds_b64:
            try:
                # Try base64 first (for Render)
                creds_json = base64.b64decode(creds_b64).decode()
                credentials_dict = json.loads(creds_json)
                credentials = ServiceAccountCredentials.from_json_keyfile_dict(
                    credentials_dict, SCOPES
                )
                gc = gspread.authorize(credentials)
                print("✓ Authenticated with Google Sheets (base64)")
                return gc
            except Exception as e:
                print(f"Base64 failed: {e}")
                # Try plain JSON as fallback
                try:
                    credentials_dict = json.loads(creds_b64)
                    credentials = ServiceAccountCredentials.from_json_keyfile_dict(
                        credentials_dict, SCOPES
                    )
                    gc = gspread.authorize(credentials)
                    print("✓ Authenticated with Google Sheets (plain JSON)")
                    return gc
                except Exception as e2:
                    print(f"Plain JSON also failed: {e2}")
        
        # Fallback to local file
        credentials = ServiceAccountCredentials.from_json_keyfile_name(
            SERVICE_ACCOUNT_FILE, SCOPES
        )
        gc = gspread.authorize(credentials)
        print("✓ Authenticated with Google Sheets (local file)")
        return gc
        
    except Exception as e:
        print(f"Error authenticating with Google Sheets API: {e}")
        return None

def sync_payment_to_google_sheet(payment_data):
    """
    Sync a single payment record to Google Sheets Payments worksheet.
    This ensures payments are saved to Google Sheet for other apps to read.
    Returns True if successful, False otherwise.
    """
    if not UNIFIED_SHEET_ENABLED or not UNIFIED_GOOGLE_SHEET_ID:
        print("Unified sheet sync is disabled - payment not synced to Google Sheets")
        return False
    
    if 'payments' not in SHEET_WORKBOOKS:
        print("Payments worksheet not configured in SHEET_WORKBOOKS")
        return False
    
    worksheet_name = SHEET_WORKBOOKS['payments']
    
    try:
        gc = get_google_sheet_client()
        if not gc:
            print("Failed to get Google Sheets client for payment sync")
            return False
        
        # Open the spreadsheet
        spreadsheet = gc.open_by_key(UNIFIED_GOOGLE_SHEET_ID)
        
        # Get or create the worksheet
        try:
            worksheet = spreadsheet.worksheet(worksheet_name)
        except gspread.exceptions.WorksheetNotFound:
            # Create the worksheet if it doesn't exist
            spreadsheet.add_worksheet(title=worksheet_name, rows=1000, cols=12)
            worksheet = spreadsheet.worksheet(worksheet_name)
            # Add headers
            headers = ['id', 'student_id', 'student_name', 'fee_type', 'amount', 
                       'payment_date', 'payment_method', 'receipt_number', 
                       'transaction_ref', 'status', 'notes', 'created_at']
            worksheet.append_row(headers)
            print(f"Created new '{worksheet_name}' worksheet with headers")
        
        # Check if worksheet has headers
        existing_headers = worksheet.row_values(1) if worksheet.row_count > 0 else []
        
        # Prepare row data - handle all possible keys
        row_data = [
            str(payment_data.get('id', '')),
            str(payment_data.get('student_id', '')),
            str(payment_data.get('student_name', '')),
            str(payment_data.get('fee_type', '')),
            str(payment_data.get('amount', 0)),
            str(payment_data.get('payment_date', '')),
            str(payment_data.get('payment_method', '')),
            str(payment_data.get('receipt_number', '')),
            str(payment_data.get('transaction_ref', '')),
            str(payment_data.get('status', 'completed')),
            str(payment_data.get('notes', '')),
            str(payment_data.get('created_at', datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        ]
        
        # Append the payment row
        worksheet.append_row(row_data)
        print(f"Synced payment to Google Sheet: Receipt {payment_data.get('receipt_number', 'N/A')}")
        
        return True
        
    except Exception as e:
        print(f"Error syncing payment to Google Sheet: {e}")
        return False


def save_store_item_to_google_sheet(name, category, unit, quantity, min_threshold):
    """
    Save a new store item directly to Google Sheets (online mode).
    This is the primary way to save store items - no local save first.
    Returns True if successful, False otherwise.
    """
    if not UNIFIED_SHEET_ENABLED or not UNIFIED_GOOGLE_SHEET_ID:
        print("Unified sheet sync is disabled - store item not saved to Google Sheets")
        return False
    
    if 'store_items' not in SHEET_WORKBOOKS:
        print("Store Items worksheet not configured in SHEET_WORKBOOKS")
        return False
    
    worksheet_name = SHEET_WORKBOOKS['store_items']
    
    try:
        gc = get_google_sheet_client()
        if not gc:
            print("Failed to get Google Sheets client for store item save")
            return False
        
        # Open the spreadsheet
        spreadsheet = gc.open_by_key(UNIFIED_GOOGLE_SHEET_ID)
        
        # Get or create the worksheet
        try:
            worksheet = spreadsheet.worksheet(worksheet_name)
        except gspread.exceptions.WorksheetNotFound:
            # Create the worksheet if it doesn't exist
            spreadsheet.add_worksheet(title=worksheet_name, rows=1000, cols=8)
            worksheet = spreadsheet.worksheet(worksheet_name)
            # Add headers
            headers = ['id', 'name', 'category', 'unit', 'quantity', 
                       'min_threshold', 'created_at', 'updated_at']
            worksheet.append_row(headers)
            print(f"Created new '{worksheet_name}' worksheet with headers")
        
        # Generate unique ID for the item
        item_id = f"STORE-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        # Prepare row data
        row_data = [
            item_id,
            str(name),
            str(category),
            str(unit),
            str(quantity),
            str(min_threshold),
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            ''
        ]
        
        # Append the row to the sheet
        worksheet.append_row(row_data)
        print(f"Store item saved to Google Sheets: {name} ({item_id})")
        return True
        
    except Exception as e:
        print(f"Error saving store item to Google Sheet: {e}")
        return False


def read_store_items_from_google_sheet():
    """
    Read store items directly from Google Sheets (online mode).
    Returns a list of dictionaries with item data, or None if failed.
    """
    if not UNIFIED_SHEET_ENABLED or not UNIFIED_GOOGLE_SHEET_ID:
        print("Unified sheet sync is disabled - cannot read from Google Sheets")
        return None
    
    if 'store_items' not in SHEET_WORKBOOKS:
        print("Store Items worksheet not configured in SHEET_WORKBOOKS")
        return None
    
    worksheet_name = SHEET_WORKBOOKS['store_items']
    
    try:
        gc = get_google_sheet_client()
        if not gc:
            print("Failed to get Google Sheets client for reading store items")
            return None
        
        # Open the spreadsheet
        spreadsheet = gc.open_by_key(UNIFIED_GOOGLE_SHEET_ID)
        
        # Get the worksheet
        try:
            worksheet = spreadsheet.worksheet(worksheet_name)
        except gspread.exceptions.WorksheetNotFound:
            print(f"Worksheet '{worksheet_name}' not found in Google Sheet")
            return None
        
        # Get all records
        records = worksheet.get_all_records()
        print(f"Read {len(records)} store items from Google Sheets")
        return records
        
    except Exception as e:
        print(f"Error reading store items from Google Sheet: {e}")
        return None


def read_store_transactions_from_google_sheet():
    """
    Read store transactions directly from Google Sheets (online mode).
    Returns a list of dictionaries with transaction data, or None if failed.
    """
    if not UNIFIED_SHEET_ENABLED or not UNIFIED_GOOGLE_SHEET_ID:
        print("Unified sheet sync is disabled - cannot read transactions from Google Sheets")
        return None
    
    if 'store_transactions' not in SHEET_WORKBOOKS:
        print("Store Transactions worksheet not configured in SHEET_WORKBOOKS")
        return None
    
    worksheet_name = SHEET_WORKBOOKS['store_transactions']
    
    try:
        gc = get_google_sheet_client()
        if not gc:
            print("Failed to get Google Sheets client for reading transactions")
            return None
        
        # Open the spreadsheet
        spreadsheet = gc.open_by_key(UNIFIED_GOOGLE_SHEET_ID)
        
        # Get the worksheet
        try:
            worksheet = spreadsheet.worksheet(worksheet_name)
        except gspread.exceptions.WorksheetNotFound:
            print(f"Worksheet '{worksheet_name}' not found in Google Sheet")
            return None
        
        # Get all records
        records = worksheet.get_all_records()
        print(f"Read {len(records)} store transactions from Google Sheets")
        return records
        
    except Exception as e:
        print(f"Error reading store transactions from Google Sheet: {e}")
        return None


def save_store_transaction_to_google_sheet(item_id, item_name, transaction_type, quantity, recipient, notes):
    """
    Save a store transaction directly to Google Sheets (online mode).
    Returns True if successful, False otherwise.
    """
    if not UNIFIED_SHEET_ENABLED or not UNIFIED_GOOGLE_SHEET_ID:
        print("Unified sheet sync is disabled - transaction not saved to Google Sheets")
        return False
    
    if 'store_transactions' not in SHEET_WORKBOOKS:
        print("Store Transactions worksheet not configured in SHEET_WORKBOOKS")
        return False
    
    worksheet_name = SHEET_WORKBOOKS['store_transactions']
    
    try:
        gc = get_google_sheet_client()
        if not gc:
            print("Failed to get Google Sheets client for transaction save")
            return False
        
        # Open the spreadsheet
        spreadsheet = gc.open_by_key(UNIFIED_GOOGLE_SHEET_ID)
        
        # Get or create the worksheet
        try:
            worksheet = spreadsheet.worksheet(worksheet_name)
        except gspread.exceptions.WorksheetNotFound:
            # Create the worksheet if it doesn't exist
            spreadsheet.add_worksheet(title=worksheet_name, rows=1000, cols=10)
            worksheet = spreadsheet.worksheet(worksheet_name)
            # Add headers
            headers = ['id', 'item_id', 'item_name', 'transaction_type', 'quantity', 
                       'recipient', 'notes', 'issued_by', 'created_at', 'updated_at']
            worksheet.append_row(headers)
            print(f"Created new '{worksheet_name}' worksheet with headers")
        
        # Generate unique ID for the transaction
        txn_id = f"TXN-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        # Prepare row data
        row_data = [
            txn_id,
            str(item_id),
            str(item_name),
            str(transaction_type),
            str(quantity),
            str(recipient),
            str(notes),
            session.get('staff_username', session.get('admin_username', 'Admin')),
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            ''
        ]
        
        # Append the row to the sheet
        worksheet.append_row(row_data)
        print(f"Store transaction saved to Google Sheets: {transaction_type} - {item_name}")
        return True
        
    except Exception as e:
        print(f"Error saving transaction to Google Sheet: {e}")
        return False


def update_store_item_in_google_sheet(item_id, new_quantity, updated_at=None):
    """
    Update a store item's quantity directly in Google Sheets (online mode).
    This is used for both restock (add quantity) and issue (deduct quantity).
    The new_quantity is the final quantity value, not the delta.
    Returns True if successful, False otherwise.
    """
    if not UNIFIED_SHEET_ENABLED or not UNIFIED_GOOGLE_SHEET_ID:
        print("Unified sheet sync is disabled - cannot update store item in Google Sheets")
        return False
    
    if 'store_items' not in SHEET_WORKBOOKS:
        print("Store Items worksheet not configured in SHEET_WORKBOOKS")
        return False
    
    worksheet_name = SHEET_WORKBOOKS['store_items']
    
    if updated_at is None:
        updated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    try:
        gc = get_google_sheet_client()
        if not gc:
            print("Failed to get Google Sheets client for store item update")
            return False
        
        # Open the spreadsheet
        spreadsheet = gc.open_by_key(UNIFIED_GOOGLE_SHEET_ID)
        
        # Get the worksheet
        try:
            worksheet = spreadsheet.worksheet(worksheet_name)
        except gspread.exceptions.WorksheetNotFound:
            print(f"Worksheet '{worksheet_name}' not found in Google Sheet")
            return False
        
        # Get all records to find the item
        records = worksheet.get_all_records()
        
        # Find the row number (2 because row 1 is header)
        row_num = None
        for idx, record in enumerate(records, start=2):  # start=2 because row 1 is header
            if str(record.get('id', '')) == str(item_id):
                row_num = idx
                break
        
        if row_num is None:
            print(f"Store item with ID '{item_id}' not found in Google Sheet")
            return False
        
        # Update the quantity and updated_at columns using cell values directly
        # Get all values in the row to determine correct column positions
        row_values = worksheet.row_values(row_num)
        
        # Find the column positions by header row
        headers = worksheet.row_values(1)
        
        # Find quantity column index (1-based for gspread)
        quantity_col = headers.index('quantity') + 1 if 'quantity' in headers else 5
        updated_at_col = headers.index('updated_at') + 1 if 'updated_at' in headers else 8
        
        # Update cells
        worksheet.update_cell(row_num, quantity_col, str(new_quantity))
        worksheet.update_cell(row_num, updated_at_col, updated_at)
        
        print(f"Updated store item in Google Sheets: {item_id} -> quantity: {new_quantity}")
        return True
        
    except Exception as e:
        print(f"Error updating store item in Google Sheet: {e}")
        return False


def cleanup_store_items_data():
    """
    Clean up corrupted data in the store items Google Sheet.
    Fixes cases where unit values ended up in quantity column and vice versa.
    Returns number of rows fixed, or -1 on error.
    """
    if not UNIFIED_SHEET_ENABLED or not UNIFIED_GOOGLE_SHEET_ID:
        print("Unified sheet sync is disabled")
        return -1
    
    if 'store_items' not in SHEET_WORKBOOKS:
        print("Store Items worksheet not configured")
        return -1
    
    worksheet_name = SHEET_WORKBOOKS['store_items']
    valid_units = ['kg', 'pieces', 'packs', 'boxes', 'liters', 'ml', 'grams', 'cartons', 'bundles', 'reams', 'pairs', 'sets', 'bags', 'bottles', 'cans', 'rolls', 'sheets', 'meters', 'cm', 'units', 'dozens']
    
    try:
        gc = get_google_sheet_client()
        if not gc:
            print("Failed to get Google Sheets client")
            return -1
        
        spreadsheet = gc.open_by_key(UNIFIED_GOOGLE_SHEET_ID)
        
        try:
            worksheet = spreadsheet.worksheet(worksheet_name)
        except gspread.exceptions.WorksheetNotFound:
            print(f"Worksheet '{worksheet_name}' not found")
            return -1
        
        # Get headers to find column positions
        headers = worksheet.row_values(1)
        
        # Find column indices (1-based for gspread)
        try:
            id_col = headers.index('id') + 1
            name_col = headers.index('name') + 1
            category_col = headers.index('category') + 1
            unit_col = headers.index('unit') + 1
            quantity_col = headers.index('quantity') + 1
            min_threshold_col = headers.index('min_threshold') + 1
        except ValueError as e:
            print(f"Missing required header column: {e}")
            return -1
        
        # Get all records
        records = worksheet.get_all_records()
        fixed_count = 0
        
        for idx, record in enumerate(records, start=2):  # start=2 because row 1 is header
            quantity_val = str(record.get('quantity', ''))
            unit_val = str(record.get('unit', ''))
            
            # Check if quantity column contains a unit string
            needs_swap = False
            
            if quantity_val.lower() in [u.lower() for u in valid_units]:
                # Quantity has a unit value, need to swap
                needs_swap = True
            elif unit_val and unit_val.replace('.', '').replace('-', '').isdigit():
                # Unit has a numeric value, need to swap
                needs_swap = True
            elif quantity_val and not quantity_val.replace('.', '').replace('-', '').isdigit():
                # Quantity has non-numeric text that isn't a valid unit
                needs_swap = True
            
            if needs_swap and unit_val and unit_val not in ['', 'N/A']:
                # Swap the values
                print(f"Fixing row {idx}: swapping quantity='{quantity_val}' with unit='{unit_val}'")
                worksheet.update_cell(idx, quantity_col, str(unit_val))
                worksheet.update_cell(idx, unit_col, str(quantity_val))
                fixed_count += 1
        
        print(f"Data cleanup complete. Fixed {fixed_count} rows.")
        return fixed_count
        
    except Exception as e:
        print(f"Error cleaning up store items data: {e}")
        return -1


def sync_all_payments_to_google_sheet():
    """
    Sync ALL payment records from local Excel to Google Sheets.
    This is useful for initial sync or recovery.
    Returns True if successful, False otherwise.
    """
    if not UNIFIED_SHEET_ENABLED or not UNIFIED_GOOGLE_SHEET_ID:
        print("Unified sheet sync is disabled")
        return False
    
    worksheet_name = SHEET_WORKBOOKS.get('payments', 'Payments')
    
    try:
        gc = get_google_sheet_client()
        if not gc:
            print("Failed to get Google Sheets client")
            return False
        
        # Open the spreadsheet
        spreadsheet = gc.open_by_key(UNIFIED_GOOGLE_SHEET_ID)
        
        # Get or create the worksheet
        try:
            worksheet = spreadsheet.worksheet(worksheet_name)
        except gspread.exceptions.WorksheetNotFound:
            spreadsheet.add_worksheet(title=worksheet_name, rows=10000, cols=12)
            worksheet = spreadsheet.worksheet(worksheet_name)
            # Add headers
            headers = ['id', 'student_id', 'student_name', 'fee_type', 'amount', 
                       'payment_date', 'payment_method', 'receipt_number', 
                       'transaction_ref', 'status', 'notes', 'created_at']
            worksheet.append_row(headers)
        
        # Get all payments from local Excel
        all_payments = SchoolPayment.all()
        
        if not all_payments:
            print("No payments to sync")
            return True
        
        # Clear existing data (keep header)
        worksheet.clear()
        headers = ['id', 'student_id', 'student_name', 'fee_type', 'amount', 
                   'payment_date', 'payment_method', 'receipt_number', 
                   'transaction_ref', 'status', 'notes', 'created_at']
        worksheet.append_row(headers)
        
        # Write all payments
        for payment in all_payments:
            row_data = [
                str(payment.get('id', '')),
                str(payment.get('student_id', '')),
                str(payment.get('student_name', '')),
                str(payment.get('fee_type', '')),
                str(payment.get('amount', 0)),
                str(payment.get('payment_date', '')),
                str(payment.get('payment_method', '')),
                str(payment.get('receipt_number', '')),
                str(payment.get('transaction_ref', '')),
                str(payment.get('status', 'completed')),
                str(payment.get('notes', '')),
                str(payment.get('created_at', ''))
            ]
            worksheet.append_row(row_data)
        
        print(f"Synced {len(all_payments)} payments to Google Sheet")
        return True
        
    except Exception as e:
        print(f"Error syncing all payments to Google Sheet: {e}")
        return False


@app.route('/admin/sync_payments', methods=['GET'])
def admin_sync_payments():
    """
    Route to manually sync all payments to Google Sheet.
    Accessible from admin dashboard.
    """
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    success = sync_all_payments_to_google_sheet()
    
    if success:
        flash('All payments synced to Google Sheets successfully!', 'success')
    else:
        flash('Failed to sync payments. Check Google Sheet configuration.', 'danger')
    
    return redirect(url_for('admin_finance'))
    
    if data_type not in SHEET_WORKBOOKS:
        print(f"Unknown data type for unified sync: {data_type}")
        return None
    
    worksheet_name = SHEET_WORKBOOKS[data_type]
    
    try:
        gc = get_google_sheet_client()
        if not gc:
            print("Failed to get Google Sheets client for unified sync")
            return None
        
        # Open the spreadsheet
        spreadsheet = gc.open_by_key(UNIFIED_GOOGLE_SHEET_ID)
        
        # Get the worksheet
        try:
            worksheet = spreadsheet.worksheet(worksheet_name)
        except gspread.exceptions.WorksheetNotFound:
            print(f"Worksheet '{worksheet_name}' not found in unified sheet")
            return None
        
        # Get all values
        records = worksheet.get_all_records()
        
        if not records:
            print(f"No records found in {worksheet_name}")
            return None
        
        # Convert to DataFrame
        df = pd.DataFrame(records)
        
        # Save to local Excel
        excel_path = get_excel_path(data_type)
        df.to_excel(excel_path, index=False)
        print(f"Synced {len(df)} records from '{worksheet_name}' to {data_type}")
        
        return df
        
    except Exception as e:
        print(f"Error syncing {data_type} from unified sheet: {e}")
        return None

def sync_all_data_from_unified_sheet():
    """
    Sync ALL data types from the unified Google Sheet to local Excel files.
    Returns a dict with status for each data type.
    """
    results = {}
    
    if not UNIFIED_SHEET_ENABLED or not UNIFIED_GOOGLE_SHEET_ID:
        return {'error': 'Unified sheet sync is disabled'}
    
    gc = get_google_sheet_client()
    if not gc:
        return {'error': 'Failed to authenticate with Google Sheets'}
    
    try:
        spreadsheet = gc.open_by_key(UNIFIED_GOOGLE_SHEET_ID)
        
        for data_type, worksheet_name in SHEET_WORKBOOKS.items():
            try:
                worksheet = spreadsheet.worksheet(worksheet_name)
                records = worksheet.get_all_records()
                
                if records:
                    df = pd.DataFrame(records)
                    excel_path = get_excel_path(data_type)
                    df.to_excel(excel_path, index=False)
                    results[data_type] = {'success': True, 'count': len(df)}
                else:
                    results[data_type] = {'success': True, 'count': 0, 'message': 'No records'}
                    
            except gspread.exceptions.WorksheetNotFound:
                results[data_type] = {'success': False, 'message': 'Worksheet not found'}
            except Exception as e:
                results[data_type] = {'success': False, 'message': str(e)}
        
        return results
        
    except Exception as e:
        return {'error': str(e)}

def ensure_worksheet_exists(gc, spreadsheet, worksheet_name, headers):
    """
    Check if worksheet exists, create if not, and add/update headers.
    """
    try:
        worksheet = spreadsheet.worksheet(worksheet_name)
        # Worksheet exists - ensure headers are set
        if headers:
            try:
                # Check if headers are already set by reading first row
                first_row = worksheet.get_values('A1:Z1')
                if not first_row or not first_row[0] or first_row[0][0] == '':
                    # Headers are empty, update them
                    worksheet.update(values=[headers], range_name='A1')
            except:
                # If we can't read or update, just continue
                pass
        return worksheet
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=worksheet_name, rows=1000, cols=20)
        if headers:
            worksheet.update(values=[headers], range_name='A1')
        return worksheet

def auto_create_all_worksheets(gc, spreadsheet):
    """
    Automatically create all required worksheets in the Google Sheet.
    Returns a dict with status for each worksheet.
    """
    results = {}
    
    for data_type, worksheet_name in SHEET_WORKBOOKS.items():
        try:
            # Get headers from local Excel file
            excel_path = get_excel_path(data_type)
            headers = []
            if os.path.exists(excel_path):
                df = pd.read_excel(excel_path)
                if not df.empty:
                    headers = df.columns.tolist()
            
            # Ensure worksheet exists (creates if not)
            worksheet = ensure_worksheet_exists(gc, spreadsheet, worksheet_name, headers)
            results[worksheet_name] = {'success': True, 'message': 'Ready'}
            
        except Exception as e:
            results[worksheet_name] = {'success': False, 'message': str(e)}
    
    return results

def push_data_to_unified_sheet(data_type):
    """
    Push local data to a specific worksheet in the unified Google Sheet.
    Automatically creates worksheet if it doesn't exist.
    """
    if not UNIFIED_SHEET_ENABLED or not UNIFIED_GOOGLE_SHEET_ID:
        print(f"[PUSH ERROR] Unified sheet disabled or no Sheet ID configured")
        return {'success': False, 'message': 'Unified sheet sync is disabled'}
    
    if data_type not in SHEET_WORKBOOKS:
        print(f"[PUSH ERROR] Unknown data type: {data_type}")
        return {'success': False, 'message': f'Unknown data type: {data_type}'}
    
    worksheet_name = SHEET_WORKBOOKS[data_type]
    print(f"[PUSH START] Processing: {data_type} -> {worksheet_name}")
    
    try:
        gc = get_google_sheet_client()
        if not gc:
            print(f"[PUSH ERROR] Failed to authenticate with Google Sheets")
            return {'success': False, 'message': 'Failed to authenticate with Google Sheets'}
        
        # Get local data
        excel_path = get_excel_path(data_type)
        print(f"[PUSH DEBUG] Excel path: {excel_path}")
        
        if not os.path.exists(excel_path):
            print(f"[PUSH ERROR] File not found: {excel_path}")
            return {'success': False, 'message': f'Local file not found for {data_type}'}
        
        df = pd.read_excel(excel_path)
        print(f"[PUSH DEBUG] Loaded {len(df)} rows, columns: {df.columns.tolist()}")
        
        if df.empty:
            print(f"[PUSH ERROR] DataFrame is empty for {data_type}")
            return {'success': False, 'message': f'No data to push for {data_type}'}
        
        # Open the spreadsheet
        print(f"[PUSH DEBUG] Opening spreadsheet: {UNIFIED_GOOGLE_SHEET_ID}")
        spreadsheet = gc.open_by_key(UNIFIED_GOOGLE_SHEET_ID)
        
        # Auto-create the worksheet with headers
        headers = df.columns.tolist()
        print(f"[PUSH DEBUG] Headers: {headers}")
        worksheet = ensure_worksheet_exists(gc, spreadsheet, worksheet_name, headers)
        print(f"[PUSH DEBUG] Worksheet ready: {worksheet_name}")
        
        # Prepare data - convert all values to strings and handle NaN
        data_rows = []
        for _, row in df.iterrows():
            clean_row = []
            for val in row:
                if pd.isna(val):
                    clean_row.append('')
                elif isinstance(val, (int, float)):
                    clean_row.append(str(val))
                else:
                    clean_row.append(str(val))
            data_rows.append(clean_row)
        
        all_values = [headers] + data_rows
        print(f"[PUSH DEBUG] Total rows to push: {len(all_values)} (including header)")
        
        # Clear the entire worksheet first to remove old data
        worksheet.clear()
        print(f"[PUSH DEBUG] Cleared worksheet")
        
        # Calculate the proper range
        num_rows = len(all_values)
        num_cols = len(headers)
        
        # Handle columns beyond Z
        if num_cols <= 26:
            last_col_letter = chr(ord('A') + num_cols - 1)
        else:
            # For columns beyond Z (AA, AB, etc.)
            first_letter = chr(ord('A') + (num_cols // 26) - 1)
            second_letter = chr(ord('A') + (num_cols % 26) - 1)
            last_col_letter = f"{first_letter}{second_letter}"
        
        range_end = f'{last_col_letter}{num_rows}'
        print(f"[PUSH DEBUG] Update range: A1:{range_end}")
        
        # Update all values in one call
        worksheet.update(values=all_values, range_name=f'A1:{range_end}')
        print(f"[PUSH SUCCESS] Pushed {len(df)} records to {worksheet_name}")
        
        return {'success': True, 'message': f'Pushed {len(df)} records to {worksheet_name}', 'count': len(df)}
        
    except Exception as e:
        print(f"[PUSH ERROR] Exception: {str(e)}")
        import traceback
        traceback.print_exc()
        return {'success': False, 'message': f'Error: {str(e)}'}

# --- Website Domain Configuration ---
# IMPORTANT: Replace with your actual website domain (e.g., 'https://your-school-website.com')
WEBSITE_DOMAIN = "https://flask-sms-app.onrender.com" # Replace with your actual domain in production

# --- Admin Password Hashing ---
# Hash for the password 'gyedu2025'
# In a real application, generate this hash once and store it securely (e.g., in environment variables or a config file).
ADMIN_PASSWORD_HASH = generate_password_hash('gyedu2025') # Hashing the password 'gyedu2025'
print(f"Admin password hash (for 'gyedu2025'): {ADMIN_PASSWORD_HASH}") # Print hash for verification

# --- HOD Credentials Configuration ---
# Store HOD credentials (username: hashed_password, department)
# In production, use a database instead of this dictionary
HOD_CREDENTIALS = {}

# Function to add a new HOD
def add_hod(username, password, department):
    """Add a new HOD account with hashed password to staff_users.xlsx and sync to Google Sheet."""
    file_path = get_excel_path('staff_accounts')
    hashed_pw = generate_password_hash(password)
    
    # Prepare HOD data
    new_hod = pd.DataFrame([{
        'username': username.strip(), 
        'password': hashed_pw, 
        'role': 'hod',  # Use 'hod' as the role
        'department': department.strip().lower(),
        'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }])

    try:
        # Save to Excel file
        if os.path.exists(file_path):
            df = pd.read_excel(file_path)
            # Remove existing user if re-registering
            df = df[df['username'] != username]
            df = pd.concat([df, new_hod], ignore_index=True)
        else:
            df = new_hod
        
        df.to_excel(file_path, index=False)
        
        # Also update in-memory dict for backward compatibility
        HOD_CREDENTIALS[username] = {
            'password': hashed_pw,
            'department': department
        }
        
        # Sync to Google Sheets
        try:
            sync_result = push_data_to_unified_sheet('staff_accounts')
            if sync_result.get('success'):
                print(f"HOD account '{username}' synced to Google Sheets successfully!")
            else:
                print(f"HOD account saved locally, but Google Sheets sync failed: {sync_result.get('message')}")
        except Exception as gs_error:
            print(f"Google Sheets sync error for HOD account: {gs_error}")
        
        return True
        
    except PermissionError:
        return False
    except Exception as e:
        print(f"Error creating HOD account: {e}")
        return False

# Load HOD accounts from staff_users.xlsx on startup (if file exists)
def load_hod_accounts_from_file():
    """Load HOD accounts from staff_users.xlsx file."""
    file_path = get_excel_path('staff_accounts')
    if os.path.exists(file_path):
        try:
            df = pd.read_excel(file_path)
            hod_df = df[df['role'].str.strip().str.lower() == 'hod']
            for _, row in hod_df.iterrows():
                HOD_CREDENTIALS[row['username']] = {
                    'password': row['password'],
                    'department': row.get('department', '')
                }
            print(f"Loaded {len(HOD_CREDENTIALS)} HOD accounts from file.")
        except Exception as e:
            print(f"Error loading HOD accounts from file: {e}")

# Load HOD accounts on startup
load_hod_accounts_from_file()

print(f"HOD accounts loaded: {list(HOD_CREDENTIALS.keys())}")

# --- File Upload Configuration ---
# Create an 'uploads' directory if it doesn't exist
UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx', 'xls', 'xlsx'}

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- Department Share Link Configuration ---
# Store for share links (in production, use a database)
DEPARTMENT_SHARE_LINKS = {}

# Secret key for signing share tokens (in production, use a secure random key)
SHARE_TOKEN_SECRET = 'your_share_token_secret_key_change_this_in_production'

def generate_share_token(department, year, semester):
    """Generate a unique, secure token for sharing."""
    data = f"{department}-{year}-{semester}-{datetime.now().isoformat()}"
    token = hashlib.sha256((data + SHARE_TOKEN_SECRET).encode()).hexdigest()[:32]
    return token

def create_share_link(department, year, semester, expires_in_days=7):
    """Create a shareable link for a department head."""
    token = generate_share_token(department, year, semester)
    expires_at = datetime.now() + timedelta(days=expires_in_days)
    
    DEPARTMENT_SHARE_LINKS[token] = {
        'department': department,
        'year': year,
        'semester': semester,
        'expires_at': expires_at,
        'used': False
    }
    
    share_url = url_for('department_upload', token=token, _external=True)
    return share_url, token, expires_at

def validate_share_token(token):
    """Validate a share token and return its data if valid."""
    if token not in DEPARTMENT_SHARE_LINKS:
        return None, "Invalid token"
    
    link_data = DEPARTMENT_SHARE_LINKS[token]
    
    if link_data['used']:
        return None, "This link has already been used"
    
    if datetime.now() > link_data['expires_at']:
        return None, "This link has expired"
    
    return link_data, None

def mark_token_used(token):
    """Mark a share token as used."""
    if token in DEPARTMENT_SHARE_LINKS:
        DEPARTMENT_SHARE_LINKS[token]['used'] = True

# --- Dynamic Metadata Configuration ---
# Define the structure for a single, generic subject's details.
# The keys are internal identifiers, the values are the *suffixes* found in the column header.
GENERIC_SUBJECT_SCORE_TYPES = {
    'Exams Score': 'Exams Score',
    'Class Score': 'Class Score',
    'Total Score': 'Total Score',
    'Remarks': 'Remarks',
    'Grade' : 'Grade' 
}

# Define the CORE subjects. These are universal across all departments.
# IMPORTANT: Add ALL your school's CORE subjects here.
CORE_SUBJECT_NAMES = ['Math', 'English', 'Science','Social','ICT','Entrepreneur'] # EXAMPLE: Add your actual CORE subjects

# Define the ELECTIVE subjects PER DEPARTMENT.
# IMPORTANT: Map each department name (as it appears in your 'Student Department' column)
# to a list of its specific elective subjects.
ELECTIVE_SUBJECT_NAMES_BY_DEPARTMENT = {
    'General Science': ['Physics', 'Chemistry', 'Biology','Elective Maths'],
    'General Arts': ['History', 'Literature', 'Government'],
    'Business': ['Accounting', 'Economics', 'Business Management','Elective Maths'],
    'Home Science': ['Food & Nutrition', 'Textiles', 'Management in Living','Chemistry'],
    'General Agric': ['General Agriculture', 'Crop Husbandry', 'Animal Husbandry','Chemistry','Elective Maths'],
    'Other': ['General Knowledge in Art'] ,# Example for departments not explicitly listed,
    'electricals': ['Electrical installation', 'Principles of electrical','practicals'],
    'welding': ['Welding Fabrication', 'welding principles','practicals'],
    'fashion': ['Garment design', 'Garment Construction', 'practicals'],
    'plumbing': ['Plumbing Principles', 'Plumbing Technology','practicals'],
    'Catering': ['Catering Principles', 'Catering Production','practicals'],
    'building': ['Construction Practice', 'Construction Materials','practicals'],
    'wood': ['Wood Principles', 'Wood Technology','practicals'],
}

# Combine all unique subject names for header parsing
ALL_GENERIC_SUBJECT_NAMES = sorted(list(set(CORE_SUBJECT_NAMES + [
    subject for sublist in ELECTIVE_SUBJECT_NAMES_BY_DEPARTMENT.values() for subject in sublist
])))

# Define the generic semester types used in your sheet (e.g., 'Semester 1', 'Sem 2').
# IMPORTANT: Add ALL your school's generic semester types here.
GENERIC_SEMESTER_TYPES = ['Semester 1', 'Semester 2', 'Sem 1', 'Sem 2'] # All possible semester formats

# Default years to show if no data is loaded yet
DEFAULT_YEARS = [str(year) for year in range(2020, 2041)]  # 2020 to 2040

# Global variables to be populated dynamically based on Google Sheet headers
AVAILABLE_YEARS = []
AVAILABLE_GENERIC_SEMESTER_TYPES = [] # e.g., 'Semester 1', 'Semester 2'
AVAILABLE_DEPARTMENTS = sorted(list(ELECTIVE_SUBJECT_NAMES_BY_DEPARTMENT.keys())) # New: For department dropdown
COLUMN_MAPPING = {} # Maps internal keys to actual Google Sheet column names
# FULL_SUBJECT_DETAILS_BY_SEMESTER_KEY now includes 'Core Subjects' and 'Elective Subjects' keys
FULL_SUBJECT_DETAILS_BY_SEMESTER_KEY = {} # Maps 'Year - Semester Type' to {'Core Subjects': {Subject -> {ScoreType -> ColName}}, 'Elective Subjects': {Subject -> {ScoreType -> ColName}}}

def initialize_sheet_metadata():
    """
    Dynamically initializes AVAILABLE_YEARS, AVAILABLE_GENERIC_SEMESTER_TYPES,
    COLUMN_MAPPING, and FULL_SUBJECT_DETAILS_BY_SEMESTER_KEY by reading data from Google Sheets.
    """
    global AVAILABLE_YEARS, AVAILABLE_GENERIC_SEMESTER_TYPES, COLUMN_MAPPING, FULL_SUBJECT_DETAILS_BY_SEMESTER_KEY

    df_headers = None
    
    try:
        if DATA_SOURCE == 'google_sheet':
            # Read from Google Sheet using the CSV export URL
            print(f"Loading headers from Google Sheet: {GOOGLE_SHEET_CSV_URL}")
            df_headers = pd.read_csv(GOOGLE_SHEET_CSV_URL, nrows=0)
            if df_headers is not None and len(df_headers.columns) > 0:
                df_headers = df_headers.columns.str.strip()
                print("Successfully loaded headers from Google Sheet")
            else:
                print("Warning: Google Sheet returned empty headers")
        else:
            print(f"Unknown DATA_SOURCE: {DATA_SOURCE}")
    
    except Exception as e:
        print(f"Error reading headers from Google Sheet: {e}")
        print("Please check that your Google Sheet is published to web and the CSV URL is correct")
    
    if df_headers is None or len(df_headers) == 0:
        print("Could not load headers. Using default values.")
        # Initialize with default values if data source cannot be read
        AVAILABLE_YEARS = DEFAULT_YEARS
        AVAILABLE_GENERIC_SEMESTER_TYPES = GENERIC_SEMESTER_TYPES
        COLUMN_MAPPING = {
            'Student ID': 'Student ID',
            'Student Name': 'Student Name',
            'Parent Phone': 'Parent Phone',
            'Student Department': 'Student Department',
        }
        FULL_SUBJECT_DETAILS_BY_SEMESTER_KEY = {}
        return

    discovered_years = set()
    discovered_generic_semester_types = set()
    temp_full_subject_details = {}
    
    # Initialize COLUMN_MAPPING with essential non-subject specific columns
    temp_column_mapping = {
        'Student ID': 'Student ID',
        'Student Name': 'Student Name',
        'Parent Phone': 'Parent Phone',
        'Student Department': 'Student Department',
    }

    # Verify essential columns exist in the actual sheet headers
    for key, col_name in list(temp_column_mapping.items()):
        if col_name not in df_headers:
            print(f"Warning: Essential column '{col_name}' for '{key}' not found in Google Sheet headers. This might affect functionality.")

    # Iterate through all column headers to discover years and semesters for subject data
    for col_header in df_headers:
        for subject_name in ALL_GENERIC_SUBJECT_NAMES: # Use the combined list
            for score_type_key, score_type_suffix in GENERIC_SUBJECT_SCORE_TYPES.items():
                pattern_prefix = f"{subject_name} {score_type_suffix} - "
                
                if col_header.startswith(pattern_prefix):
                    remainder = col_header[len(pattern_prefix):].strip()
                    parts = remainder.split(' ', 1)
                    
                    if len(parts) == 2:
                        year = parts[0].strip()
                        semester_type = parts[1].strip()

                        if not year.isdigit() or len(year) != 4:
                            continue

                        if semester_type not in GENERIC_SEMESTER_TYPES:
                            continue

                        discovered_years.add(year)
                        discovered_generic_semester_types.add(semester_type)

                        semester_key = f"{year} - {semester_type}"

                        if semester_key not in temp_full_subject_details:
                            temp_full_subject_details[semester_key] = {'Core Subjects': {}, 'Elective Subjects': {}}

                        # Determine if it's a core or elective subject
                        subject_category = None
                        if subject_name in CORE_SUBJECT_NAMES:
                            subject_category = 'Core Subjects'
                        else:
                            # Check if it's an elective in any department
                            for dept_electives in ELECTIVE_SUBJECT_NAMES_BY_DEPARTMENT.values():
                                if subject_name in dept_electives:
                                    subject_category = 'Elective Subjects'
                                    break
                        
                        if subject_category:
                            if subject_name not in temp_full_subject_details[semester_key][subject_category]:
                                temp_full_subject_details[semester_key][subject_category][subject_name] = {}

                            # Store the actual column header for this specific score type for this subject in this semester
                            temp_full_subject_details[semester_key][subject_category][subject_name][score_type_key] = col_header
                            
                            # Also add to the general COLUMN_MAPPING for data retrieval
                            temp_column_mapping[f'{semester_key} - {subject_category} - {subject_name} {score_type_key}'] = col_header
    
    # Update global variables - combine discovered with defaults to ensure all options are available
    AVAILABLE_YEARS = sorted(list(set(discovered_years) | set(DEFAULT_YEARS)))
    AVAILABLE_GENERIC_SEMESTER_TYPES = sorted(list(set(discovered_generic_semester_types) | set(GENERIC_SEMESTER_TYPES)))
    COLUMN_MAPPING.update(temp_column_mapping)
    FULL_SUBJECT_DETAILS_BY_SEMESTER_KEY = temp_full_subject_details

    print(f"Available years: {AVAILABLE_YEARS}")
    print(f"Available semester types: {AVAILABLE_GENERIC_SEMESTER_TYPES}")
    print(f"Dynamically built FULL_SUBJECT_DETAILS_BY_SEMESTER_KEY structure with {len(FULL_SUBJECT_DETAILS_BY_SEMESTER_KEY)} semester keys.")
    print(f"Dynamically updated COLUMN_MAPPING with {len(COLUMN_MAPPING)} entries.")

# Call this initialization function once at app startup
initialize_sheet_metadata()


# --- Helper to get student data by ID ---
def get_student_data_by_id(student_id):
    """Loads data and finds a student by ID."""
    df = load_results_from_sheet()
    if df.empty:
        return None, "Could not load results data."

    # Ensure the Student ID column is treated as string for reliable matching
    if COLUMN_MAPPING.get('Student ID') not in df.columns:
         return None, f"Required column '{COLUMN_MAPPING.get('Student ID')}' not found in sheet."

    df[COLUMN_MAPPING['Student ID']] = df[COLUMN_MAPPING['Student ID']].astype(str).str.strip()

    student_row = df[df[COLUMN_MAPPING['Student ID']] == student_id.strip()]

    if student_row.empty:
        return None, f"Student ID {student_id} not found."

    return student_row.iloc[0].to_dict(), None # Return student data and no error


# --- Data Loading Function ---
def load_results():
    """
    Loads student data - ALWAYS reads from Google Sheets using gspread API first,
    then caches to local Excel for offline use.
    Google Sheets is the master/external database.
    """
    print("="*60)
    print("LOADING STUDENT DATA FROM GOOGLE SHEETS")
    print("="*60)
    
    try:
        # Step 1: Get Google Sheets client using service account
        gc = get_google_sheet_client()
        if not gc:
            print("ERROR: Could not connect to Google Sheets API")
            print("Please check if service_account_credentials.json exists and is valid")
            return pd.DataFrame()
        
        print("✓ Connected to Google Sheets API")
        
        # Step 2: Open the spreadsheet using UNIFIED_GOOGLE_SHEET_ID
        spreadsheet = gc.open_by_key(UNIFIED_GOOGLE_SHEET_ID)
        print(f"✓ Opened spreadsheet: {spreadsheet.title}")
        
        # Step 3: List all available worksheets
        worksheets = spreadsheet.worksheets()
        print(f"✓ Available worksheets: {[ws.title for ws in worksheets]}")
        
        # Step 4: Access the 'Students' worksheet
        try:
            worksheet = spreadsheet.worksheet('Students')
            print("✓ Found 'Students' worksheet")
        except gspread.exceptions.WorksheetNotFound:
            print("ERROR: 'Students' worksheet not found!")
            print(f"Available worksheets: {[ws.title for ws in worksheets]}")
            print("FIX: Create a worksheet named 'Students' in your Google Sheet OR update SHEET_WORKBOOKS in app.py")
            return pd.DataFrame()
        
        # Step 5: Get all records from the worksheet
        records = worksheet.get_all_records()
        print(f"✓ Found {len(records)} records in Students worksheet")
        
        if not records:
            print("WARNING: No records found in Students worksheet")
            print("Check: Is your Google Sheet empty or does it have data rows?")
            return pd.DataFrame()
        
        # Step 6: Convert to DataFrame
        df = pd.DataFrame(records)
        
        # Step 7: Clean up column names (remove leading/trailing spaces)
        df.columns = df.columns.str.strip()
        
        print(f"✓ Loaded {len(df)} students from Google Sheet")
        print(f"✓ Columns found: {list(df.columns)}")
        
        # Step 8: Build column mapping dynamically
        global COLUMN_MAPPING
        COLUMN_MAPPING = {}
        
        for col in df.columns:
            col_clean = col.strip().lower()
            
            # Student ID mapping
            if 'student id' in col_clean or 'studentid' in col_clean or col_clean == 'id':
                COLUMN_MAPPING['Student ID'] = col
            # Student Name mapping
            elif 'student name' in col_clean or 'studentname' in col_clean or col_clean == 'name':
                COLUMN_MAPPING['Student Name'] = col
            # Department mapping
            elif 'department' in col_clean or 'dept' in col_clean:
                COLUMN_MAPPING['Student Department'] = col
            # Parent Phone mapping
            elif 'parent phone' in col_clean or 'phone' in col_clean or 'mobile' in col_clean:
                COLUMN_MAPPING['Parent Phone'] = col
            # Class mapping
            elif 'class' in col_clean:
                COLUMN_MAPPING['Class'] = col
        
        print(f"✓ Column mapping built: {COLUMN_MAPPING}")
        
        # Step 9: Always sync to local Excel for offline use
        try:
            excel_path = get_excel_path('students')
            df.to_excel(excel_path, index=False)
            print(f"✓ Cached {len(df)} students to local Excel for offline use")
        except Exception as e:
            print(f"Warning: Could not cache to local Excel: {e}")
        
        print("="*60)
        print("SUCCESS: Student data loaded from Google Sheet!")
        print("="*60)
        
        return df
        
    except Exception as e:
        print(f"ERROR loading from Google Sheet: {e}")
        import traceback
        traceback.print_exc()
        
        # Try local Excel as fallback
        print("FALLING BACK TO LOCAL EXCEL...")
        try:
            excel_path = get_excel_path('students')
            if os.path.exists(excel_path):
                df = pd.read_excel(excel_path)
                if not df.empty:
                    print(f"Loaded {len(df)} students from local Excel (FALLBACK)")
                    return df
        except Exception as e2:
            print(f"Error loading from local Excel: {e2}")
        
        return pd.DataFrame()


def load_results_from_sheet():
    """
    Loads the student results from Google Sheets using gspread API.
    Returns a pandas DataFrame with the student data.
    """
    try:
        print("="*60)
        print("LOADING STUDENTS FROM GOOGLE SHEETS")
        print("="*60)
        
        # Step 1: Get Google Sheets client
        gc = get_google_sheet_client()
        if not gc:
            print("ERROR: Could not connect to Google Sheets")
            print("Please check if service_account_credentials.json exists")
            return pd.DataFrame()
        
        print(f"✓ Connected to Google Sheets client")
        
        # Step 2: Open the spreadsheet using UNIFIED_GOOGLE_SHEET_ID
        print(f"✓ Opening spreadsheet ID: {UNIFIED_GOOGLE_SHEET_ID}")
        spreadsheet = gc.open_by_key(UNIFIED_GOOGLE_SHEET_ID)
        print(f"✓ Connected to spreadsheet: {spreadsheet.title}")
        
        # Step 3: Get list of all worksheets
        worksheets = spreadsheet.worksheets()
        print(f"✓ Available worksheets: {[ws.title for ws in worksheets]}")
        
        # Step 4: Access the 'Students' worksheet
        try:
            worksheet = spreadsheet.worksheet('Students')
            print(f"✓ Found 'Students' worksheet")
        except gspread.exceptions.WorksheetNotFound:
            print("ERROR: 'Students' worksheet not found")
            print(f"Available worksheets: {[ws.title for ws in worksheets]}")
            return pd.DataFrame()
        
        # Step 5: Get all records (both headers and data)
        records = worksheet.get_all_records()
        print(f"✓ Found {len(records)} student records")
        
        if not records:
            print("WARNING: No records found in Students worksheet")
            return pd.DataFrame()
        
        # Step 6: Convert to DataFrame
        df = pd.DataFrame(records)
        
        # Step 7: Clean up column names - remove leading/trailing spaces
        df.columns = df.columns.str.strip()
        
        print(f"✓ Loaded {len(df)} students from Google Sheet")
        print(f"✓ Columns found: {list(df.columns)}")
        
        # Step 8: Build column mapping dynamically
        build_column_mapping_from_columns(df.columns.tolist())
        
        print("="*60)
        print("SUCCESS: Student data loaded from Google Sheet!")
        print("="*60)
        
        return df
        
    except Exception as e:
        print(f"ERROR loading from Google Sheet: {e}")
        import traceback
        traceback.print_exc()
        return pd.DataFrame()

def build_column_mapping_from_columns(columns):
    """
    Build COLUMN_MAPPING dynamically based on actual column names in the sheet.
    """
    global COLUMN_MAPPING
    
    COLUMN_MAPPING = {}
    
    for col in columns:
        col_clean = col.strip().lower()
        
        # Student ID mapping
        if 'student id' in col_clean or col_clean == 'studentid' or col_clean == 'id':
            COLUMN_MAPPING['Student ID'] = col
        # Student Name mapping
        elif 'student name' in col_clean or col_clean == 'studentname' or col_clean == 'name':
            COLUMN_MAPPING['Student Name'] = col
        # Department mapping
        elif 'department' in col_clean or 'dept' in col_clean:
            COLUMN_MAPPING['Student Department'] = col
        # Parent Phone mapping
        elif 'parent phone' in col_clean or col_clean == 'phone' or col_clean == 'mobile':
            COLUMN_MAPPING['Parent Phone'] = col
        # Class mapping
        elif 'class' in col_clean:
            COLUMN_MAPPING['Class'] = col
    
    print(f"✓ Built COLUMN_MAPPING: {COLUMN_MAPPING}")

# --- Arkesel SMS Function ---
def send_sms(phone_number, message):
    """Sends an SMS message using the Arkesel API (Older GET endpoint)."""
    # Ensure phone number is in a valid format for Arkesel (e.g., starts with country code)
    # Basic cleaning: remove spaces and dashes
    cleaned_phone = str(phone_number).replace(" ", "").replace("-", "")
    # Add country code if missing (assuming Ghana +233). Adjust if needed.
    if not cleaned_phone.startswith('+'):
         # This is a simple assumption, you might need more sophisticated logic
         if cleaned_phone.startswith('0'):
             cleaned_phone = '+233' + cleaned_phone[1:] # Replace leading 0 with +233
         else:
             cleaned_phone = '+233' + cleaned_phone # Prepend +233

    # Validate phone number format (basic check)
    if not cleaned_phone or len(cleaned_phone) < 10: # Minimum length check
         print(f"Invalid phone number format: {phone_number}")
         return False, "Invalid phone number format."

    # --- Construct payload as URL parameters for the older GET endpoint ---
    payload = {
        'action': 'send-sms',
        'api_key': ARKESEL_API_KEY,
        'to': cleaned_phone,
        'from': ARKESEL_SENDER_ID,
        'sms': message
    }
    print(f"Attempting to send SMS to {cleaned_phone} with message: {message}")
    print(f"SMS API Payload (URL Params): {payload}") # Print the payload being sent

    try:
        # Use requests.get for the older endpoint and pass params
        response = requests.get(ARKESEL_SMS_URL, params=payload)

        # --- Debugging: Print status code and raw response text ---
        print(f"SMS API HTTP Status Code: {response.status_code}")
        print(f"SMS API Raw Response Text: {response.text}")
        # --- End Debugging ---

        # The older endpoint might return plain text or a different format, not always JSON.
        # We'll check for a successful status code (200) and look for indicators of success in the text.
        # You might need to adjust this success check based on actual Arkesel response for this endpoint.
        if response.status_code == 200:
            # Assuming success is indicated by a specific string in the response text
            # Replace 'SUCCESS_INDICATOR_STRING' with the actual string Arkesel returns on success
            # Common indicators might be 'OK', 'success', a specific code, etc.
            # If the response is just the message ID, checking for a non-empty text might suffice.
            if response.text and ("OK" in response.text.upper() or response.text.isdigit()): # Example check: adjust based on real response
                 return True, "SMS sent successfully!"
            else:
                 # If status is 200 but text doesn't indicate success, use the raw text as error
                 return False, f"SMS failed: API returned 200 but response indicates failure - {response.text}"
        else:
            # Handle non-200 status codes
            return False, f"SMS failed: HTTP Status Code {response.status_code} - {response.text}"

    except requests.exceptions.RequestException as e:
        print(f"Network error sending SMS: {e}")
        return False, f"Network error sending SMS: {e}"
    except Exception as e:
        print(f"An unexpected error occurred during SMS sending: {e}")
        return False, f"An unexpected error occurred during SMS sending: {e}"

# Helper function to manage staff users in Excel
def save_staff_user(username, password, role):
    file_path = get_excel_path('staff_accounts')  # Use the unified path from EXCEL_DB_DIR
    hashed_pw = generate_password_hash(password)
    new_user = pd.DataFrame([{
        'username': username.strip(), 
        'password': hashed_pw, 
        'role': role.strip().lower(),
        'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }])

    try:
        if os.path.exists(file_path):
            df = pd.read_excel(file_path)
            # Remove existing user if re-registering
            df = df[df['username'] != username]
            df = pd.concat([df, new_user], ignore_index=True)
        else:
            df = new_user
        
        # This is where the error happens if the file is open
        df.to_excel(file_path, index=False)
        
        # Sync to Google Sheets (Staff Accounts)
        try:
            staff_data = new_user.to_dict('records')[0]
            # Remove created_at from sync data if not needed in sheet
            sync_result = push_data_to_unified_sheet('staff_accounts')
            if sync_result.get('success'):
                print(f"Staff account '{username}' synced to Google Sheets successfully!")
            else:
                print(f"Staff account saved locally, but Google Sheets sync failed: {sync_result.get('message')}")
        except Exception as gs_error:
            print(f"Google Sheets sync error for staff account: {gs_error}")
        
        return True, "Staff registered successfully."

    except PermissionError:
        return False, "Error: The user database (Excel) is open in another program. Please close it and try again."
    except Exception as e:
        return False, f"An unexpected error occurred: {str(e)}"
# --- Flask Routes ---

@app.route('/')
def index():
    """School website homepage with login options."""
    return render_template('index.html')

@app.route('/ai_assistant')
def ai_assistant():
    """
    This function handles requests to the /ai_assistant URL.
    You can render a specific template for your AI assistant here,
    or simply return a placeholder message.
    """
    return "<h1>AI Assistant Page</h1><p>This feature is coming soon!</p>"
    # If you plan to have a dedicated HTML file for the AI assistant,
    # you would use:
    # return render_template('ai_assistant.html')

@app.route('/courses')
def courses():
    """Displays information about school courses."""
    # Example course data - replace with actual data from a database or config
    school_courses = [
        {'name': 'General Science', 'description': 'Focuses on Physics, Chemistry, Biology, and Elective Maths.'},
        {'name': 'General Arts', 'description': 'Covers History, Literature, and Government.'},
        {'name': 'Business', 'description': 'Includes Accounting, Economics, Business Management, and Elective Maths.'},
        {'name': 'Home Science', 'description': 'Practical skills in Food & Nutrition, Textiles, and Management in Living.'},
        {'name': 'General Agriculture', 'description': 'Study of Crop Husbandry, Animal Husbandry, and related sciences.'},
        {'name': 'Electricals', 'description': 'Electrical installation  and practical applications.'},
        {'name': 'Welding', 'description': 'Theory and practice of various welding techniques.'},
        {'name': 'Fashion', 'description': 'Garment Design, Garment construction, and fashion industry basics.'},
        {'name': 'Plumbing', 'description': 'Installation and maintenance of plumbing systems.'},
        {'name': 'Catering', 'description': 'Culinary arts, food preparation, and hospitality management.'},
        {'name': 'Other', 'description': 'General Knowledge in Art and other specialized subjects.'},
        {'name': 'Building & Construction', 'description': 'Construction Material, Construction Practice.'},

    ]
    return render_template('courses.html', courses=school_courses)


@app.route('/student_login', methods=['GET', 'POST'])
def student_login():
    """Handles student login via name and parent contact."""
    if request.method == 'POST':
        student_name = request.form.get('student_name')
        parent_phone = request.form.get('parent_phone')
        selected_year = request.form.get('year_select') # Get selected year from form
        selected_generic_semester_type = request.form.get('semester_select') # Get selected generic semester type from form
        selected_department = request.form.get('department_select') # NEW: Get selected department

        print(f"Student login attempt: Name='{student_name}', Phone='{parent_phone}', Year='{selected_year}', SemesterType='{selected_generic_semester_type}', Department='{selected_department}'") # Debug print

        if not student_name or not parent_phone or not selected_department: # NEW: Department is now required
            flash("Please enter student name, parent contact, and select your department.", 'warning')
            return render_template('student_login.html',
                                   available_years=AVAILABLE_YEARS,
                                   available_semesters=AVAILABLE_GENERIC_SEMESTER_TYPES, # Pass generic types
                                   available_departments=AVAILABLE_DEPARTMENTS, # NEW: Pass departments
                                   selected_year=selected_year,
                                   selected_semester=selected_generic_semester_type,
                                   selected_department=selected_department) # Pass selected department to re-populate dropdown

        df = load_results_from_sheet()
        if df.empty:
             flash("Could not load results data. Please try again later.", 'danger')
             print("Error: Dataframe is empty after load_results_from_sheet in student_login.") # Debug print
             return render_template('student_login.html',
                                   available_years=AVAILABLE_YEARS,
                                   available_semesters=AVAILABLE_GENERIC_SEMESTER_TYPES,
                                   available_departments=AVAILABLE_DEPARTMENTS,
                                   selected_year=selected_year,
                                   selected_semester=selected_generic_semester_type,
                                   selected_department=selected_department) # Pass selected department

        # Find the student by matching name and phone number
        # Use case-insensitive comparison for name, strip whitespace
        # Use stripped phone number for comparison, including cleaning .0
        required_student_cols = [COLUMN_MAPPING.get('Student Name'), COLUMN_MAPPING.get('Parent Phone')]
        if COLUMN_MAPPING.get('Student Department'): # Check if department column is mapped
            required_student_cols.append(COLUMN_MAPPING.get('Student Department'))
        
        required_student_cols = [col for col in required_student_cols if col is not None] # Filter out None

        if not all(col in df.columns for col in required_student_cols):
             missing_cols = [col for col in required_student_cols if col not in df.columns]
             flash(f"Required columns for verification not found in sheet: {missing_cols}.", 'danger')
             print(f"Error: Missing required student login columns: {missing_cols} in student_login.") # Debug print
             return render_template('student_login.html',
                                   available_years=AVAILABLE_YEARS,
                                   available_semesters=AVAILABLE_GENERIC_SEMESTER_TYPES,
                                   available_departments=AVAILABLE_DEPARTMENTS,
                                   selected_year=selected_year,
                                   selected_semester=selected_generic_semester_type,
                                   selected_department=selected_department) # Pass selected department

        df['_temp_name'] = df[COLUMN_MAPPING['Student Name']].astype(str).str.strip().str.lower()
        df['_temp_phone'] = df[COLUMN_MAPPING['Parent Phone']].astype(str).str.strip().replace(" ", "").replace("-", "").replace(".0", "", regex=False)

        student_row = df[
            (df['_temp_name'] == student_name.strip().lower()) &
            (df['_temp_phone'] == parent_phone.strip().replace(" ", "").replace("-", "")) # Clean input phone for comparison
        ]

        # Drop temporary columns
        df = df.drop(columns=['_temp_name', '_temp_phone'])


        if student_row.empty:
            flash("Invalid student name or parent contact.", 'danger')
            print(f"Student not found for Name='{student_name}', Phone='{parent_phone}' in student_login.") # Debug print
            return render_template('student_login.html',
                                   available_years=AVAILABLE_YEARS,
                                   available_semesters=AVAILABLE_GENERIC_SEMESTER_TYPES,
                                   available_departments=AVAILABLE_DEPARTMENTS,
                                   selected_year=selected_year,
                                   selected_semester=selected_generic_semester_type,
                                   selected_department=selected_department) # Pass selected department

        # Assuming the first match is the correct student
        student_data_dict = student_row.iloc[0].to_dict() # Renamed to avoid conflict with `student_data` passed to template
        print(f"Student found: {student_data_dict.get(COLUMN_MAPPING['Student Name'])}") # Debug print

        # Verify the selected department matches the student's actual department from the sheet
        actual_department = student_data_dict.get(COLUMN_MAPPING.get('Student Department'), 'N/A')
        if actual_department != 'N/A' and selected_department != actual_department:
            flash(f"The selected department '{selected_department}' does not match your registered department '{actual_department}'. Please try again.", 'danger')
            return render_template('student_login.html',
                                   available_years=AVAILABLE_YEARS,
                                   available_semesters=AVAILABLE_GENERIC_SEMESTER_TYPES,
                                   available_departments=AVAILABLE_DEPARTMENTS,
                                   selected_year=selected_year,
                                   selected_semester=selected_generic_semester_type,
                                   selected_department=selected_department)


        # Prepare data for display in the results.html template
        # This structure needs to match what results.html expects (lists of dicts for subjects)
        display_results = {
            'Student Name': student_data_dict.get(COLUMN_MAPPING.get('Student Name'), 'N/A'),
            'Student ID': student_data_dict.get(COLUMN_MAPPING.get('Student ID'), 'N/A'),
            'Student Department': student_data_dict.get(COLUMN_MAPPING.get('Student Department'), 'N/A'),
            'Parent Phone': student_data_dict.get(COLUMN_MAPPING.get('Parent Phone'), 'N/A'), # ADDED: Parent Phone
            'Semesters': {} # Nested dictionary for semesters
        }

        semesters_to_process = []
        if selected_year and selected_generic_semester_type:
            queried_semester_key = f"{selected_year} - {selected_generic_semester_type}"
            if queried_semester_key in FULL_SUBJECT_DETAILS_BY_SEMESTER_KEY:
                semesters_to_process.append(queried_semester_key)
            else:
                flash(f"Results for {selected_generic_semester_type} of {selected_year} are not available for {student_name}.", 'info')
        else:
            semesters_to_process = sorted(FULL_SUBJECT_DETAILS_BY_SEMESTER_KEY.keys())

        student_electives_list = ELECTIVE_SUBJECT_NAMES_BY_DEPARTMENT.get(actual_department, [])

        for semester_key in semesters_to_process:
            subjects_in_semester_template = FULL_SUBJECT_DETAILS_BY_SEMESTER_KEY.get(semester_key, {})
            
            core_subjects_data_list = []
            elective_subjects_data_list = []
            has_data_for_semester = False

            # Process Core Subjects
            for subject_name, details in subjects_in_semester_template.get('Core Subjects', {}).items():
                subject_info = {'Subject': subject_name}
                found_subject_data_for_this_subject = False
                for score_type_display, col_name in details.items():
                    if col_name in student_data_dict:
                        value = student_data_dict.get(col_name, 'N/A')
                        subject_info[score_type_display] = value
                        if value not in ['N/A', 'Column Missing', '', None]:
                            found_subject_data_for_this_subject = True
                            has_data_for_semester = True
                    else:
                        subject_info[score_type_display] = 'N/A'
                if found_subject_data_for_this_subject: # Only add if some data was found for this subject
                    core_subjects_data_list.append(subject_info)

            # Process Elective Subjects (filter by student's department)
            for subject_name, details in subjects_in_semester_template.get('Elective Subjects', {}).items():
                if subject_name in student_electives_list:
                    subject_info = {'Subject': subject_name}
                    found_subject_data_for_this_subject = False
                    for score_type_display, col_name in details.items():
                        if col_name in student_data_dict:
                            value = student_data_dict.get(col_name, 'N/A')
                            subject_info[score_type_display] = value
                            if value not in ['N/A', 'Column Missing', '', None]:
                                found_subject_data_for_this_subject = True
                                has_data_for_semester = True
                        else:
                            subject_info[score_type_display] = 'N/A'
                    if found_subject_data_for_this_subject: # Only add if some data was found for this subject
                        elective_subjects_data_list.append(subject_info)
        
            if has_data_for_semester:
                display_results['Semesters'][semester_key] = {
                    'Core Subjects': core_subjects_data_list,
                    'Elective Subjects': elective_subjects_data_list
                }
            else:
                display_results['Semesters'][semester_key] = 'Not Available'
        
        if not display_results['Semesters'] or all(v == 'Not Available' for v in display_results['Semesters'].values()):
            if selected_year and selected_generic_semester_type:
                pass
            else:
                flash("No academic results found for this student.", 'info')
            display_results['Semesters'] = {}


        print(f"Prepared display_results for student: {display_results.get('Student Name')}")

        # Check if student has paid for result viewing
        student_id_for_payment = student_data_dict.get(COLUMN_MAPPING.get('Student ID'), student_data_dict.get(COLUMN_MAPPING.get('Student Name'), ''))
        
        # Check for existing completed result viewing payment
        has_paid = False
        payments = SchoolPayment.all()
        for p in payments:
            if p.get('fee_type', '').lower() == 'result viewing' and \
               str(p.get('student_id', '')) == str(student_id_for_payment) and \
               p.get('status') == 'completed':
                has_paid = True
                session['result_payment_verified'] = True
                session['result_payment_receipt'] = p.get('receipt_number', '')
                break
        
        # Store student info in session for payment flow
        session['pending_student'] = {
            'student_name': display_results['Student Name'],
            'parent_phone': display_results['Parent Phone'],
            'student_id': student_id_for_payment,
            'display_results': display_results,
            'selected_year': selected_year,
            'selected_semester': selected_generic_semester_type,
            'selected_department': selected_department
        }
        
        if not has_paid:
            # Redirect to payment page
            flash(f'You need to pay GH₵ {RESULT_VIEWING_FEE:.2f} to view your results. This is a one-time payment for result access.', 'info')
            return redirect(url_for('student_pay_result_access'))

        # Redirect to the results page (or render it directly)
        return render_template('results.html', student_data=display_results,
                               selected_year=selected_year,
                               selected_semester=selected_generic_semester_type,
                               selected_department=selected_department)

    # Handle GET request - display student login form
    return render_template('student_login.html',
                           available_years=AVAILABLE_YEARS,
                           available_semesters=AVAILABLE_GENERIC_SEMESTER_TYPES,
                           available_departments=AVAILABLE_DEPARTMENTS)


@app.route('/admin_login', methods=['GET', 'POST'])
def admin_login():
    """Handles admin login via password."""
    if request.method == 'POST':
        password = request.form.get('password')

        # Check the submitted password against the stored hash
        if check_password_hash(ADMIN_PASSWORD_HASH, password):
            session['admin_logged_in'] = True # Set session variable on successful login
            flash('Logged in successfully!', 'success')
            return redirect(url_for('admin_dashboard')) # Redirect to admin dashboard
        else:
            flash('Invalid password.', 'danger')
            return render_template('admin_login.html') # Render login form with error

    # Handle GET request - display admin login form
    return render_template('admin_login.html')

# Helper function to get all results for a student
def get_all_student_results_by_id(student_id):
    """
    Fetches all results for a given student_id using direct column access like the working edit function.
    Filters electives based on the student's department.
    """
    df = load_results_from_sheet()
    if df.empty:
        return {'student_info': {}, 'results_by_semester': {}}

    # Get the actual Student ID column name
    student_id_col = COLUMN_MAPPING.get('Student ID', 'Student ID')
    
    if student_id_col not in df.columns:
        print(f"ERROR: Column '{student_id_col}' not found")
        print(f"Available columns: {list(df.columns)}")
        return {'student_info': {}, 'results_by_semester': {}}
    
    # Find the student using exact column match
    student_rows = df[df[student_id_col].astype(str) == str(student_id).strip()]

    if student_rows.empty:
        print(f"DEBUG: Student ID '{student_id}' not found")
        return {'student_info': {}, 'results_by_semester': {}}

    student_row = student_rows.iloc[0].to_dict()
    
    # Get student info using exact column names
    name_col = COLUMN_MAPPING.get('Student Name', 'Student Name')
    dept_col = COLUMN_MAPPING.get('Student Department', 'Student Department')
    phone_col = COLUMN_MAPPING.get('Parent Phone', 'Parent Phone')
    
    print(f"DEBUG: Fetching student: {student_id}")
    
    # Get student department for filtering electives
    student_department = student_row.get(dept_col, 'N/A') if dept_col in student_row else 'N/A'
    
    # Get the electives for this student's department
    # Normalize department name for lookup (case-insensitive)
    dept_lower = student_department.lower().strip()
    student_electives = []
    
    for dept_key, electives_list in ELECTIVE_SUBJECT_NAMES_BY_DEPARTMENT.items():
        if dept_key.lower() == dept_lower or dept_lower in dept_key.lower():
            student_electives = [e.lower() for e in electives_list]
            print(f"DEBUG: Department '{dept_key}' electives: {student_electives}")
            break
    
    # Build student info
    student_info = {
        'Student ID': student_row.get(student_id_col, student_id),
        'Student Name': student_row.get(name_col, 'N/A') if name_col in student_row else 'N/A',
        'Student Department': student_department,
        'Parent Phone': student_row.get(phone_col, 'N/A') if phone_col in student_row else 'N/A'
    }
    
    print(f"DEBUG: Student info: {student_info}")
    print(f"DEBUG: Student's electives for department '{student_department}': {student_electives}")
    
    # Get all columns in the sheet - debug output
    print("="*80)
    print("DEBUG: ALL COLUMNS IN SHEET:")
    for i, col in enumerate(df.columns):
        if '2026' in str(col):
            print(f"  [{i}] '{col}'")
    print("="*80)
    
    # Get all available semesters from columns - Match expected format "Year - Semester X"
    semesters = []
    semesters_seen = set()  # Track to avoid duplicates
    
    print("="*60)
    print("DEBUG: Looking for semesters in all columns...")
    print(f"DEBUG: Total columns in sheet: {len(df.columns)}")
    
    # Check for columns containing '2026'
    cols_with_2026 = [col for col in df.columns if '2026' in str(col)]
    print(f"DEBUG: Columns containing '2026': {cols_with_2026[:10] if cols_with_2026 else 'NONE'}")
    
    # Check for columns with underscore before Exams/Class
    cols_with_underscore = [col for col in df.columns if '_Exams' in str(col) or '_Class' in str(col)]
    print(f"DEBUG: Columns with underscore: {cols_with_underscore[:10] if cols_with_underscore else 'NONE'}")
    
    for col in df.columns:
        col_str = str(col).strip()
        
        # Check if column contains 2026
        if '2026' in col_str:
            print(f"DEBUG: Found 2026 column: '{col_str}'")
        
        if ' - ' in col_str:
            parts = col_str.rsplit(' - ', 1)
            if len(parts) == 2:
                semester_part = parts[1].strip()
                # Only accept standard semester formats (Semester 1, Semester 2, etc.)
                # EXCLUDE variants like "Semester 2.1", "Semester 1.1" which contain dots
                # These malformed semesters should NOT be included
                if '.' not in semester_part and 'Semester' in semester_part:
                    semesters.append(semester_part)
                    semesters_seen.add(semester_part)
    
    print(f"DEBUG: All unique semesters found: {sorted(list(semesters_seen))}")
    print("="*60)
    
    # Sort semesters properly (by year and semester number)
    def sort_semester_key(s):
        """Extract sorting key from semester string like '2025 Semester 1' or '2025 - Semester 1'"""
        # Clean up the string
        clean = s.replace(' - ', ' ').strip()
        parts = clean.split()
        if len(parts) >= 2:
            year = parts[0]
            sem_num = parts[-1]
            try:
                return (year, int(''.join(c for c in sem_num if c.isdigit())) if sem_num else 999)
            except:
                return (year, 999)
        return (s, 999)
    
    semesters = sorted(semesters, key=sort_semester_key)
    
    print(f"DEBUG: Found semesters: {semesters}")
    
    # Process each semester
    results_by_semester = {}
    
    for semester in semesters:
        semester_data = {'Core Subjects': [], 'Elective Subjects': []}
        subjects_dict = {}
        
        print(f"DEBUG: Processing semester '{semester}'...")
        
        # Get all columns for this semester - same logic as edit function
        for col in df.columns:
            col_str = str(col).strip()
            
            # Skip non-subject columns
            if any(skip in col_str.lower() for skip in ['student', 'name', 'phone', 'parent', 'department', 'hod']):
                continue
            
            if ' - ' not in col_str:
                continue
            
            parts = col_str.rsplit(' - ', 1)
            if len(parts) != 2:
                continue
            
            semester_part = parts[1].strip()
            if semester_part != semester:
                continue
            
            print(f"DEBUG: Found column for '{semester}': '{col_str}'")
            
            subject_and_score = parts[0].strip()
            
            # Match score types - handle both "Exams Score" and "Exams_Score" formats
            score_types = ['Exams Score', 'Class Score', 'Total Score', 'Grade', 'Remarks', 
                          'Exams_Score', 'Class_Score', 'Total_Score', 'Grade_Score', 'Remarks_Text']
            subject_name = None
            score_type = None
            
            for st in score_types:
                # Check for both space-separated and underscore-separated formats
                space_st = ' ' + st
                underscore_st = '_' + st.replace(' ', '_')
                
                if subject_and_score.endswith(space_st):
                    subject_name = subject_and_score[:-len(space_st)].strip()
                elif subject_and_score.endswith(underscore_st):
                    subject_name = subject_and_score[:-len(underscore_st)].strip()
                else:
                    continue
                    
                # Normalize subject name: remove underscores that represent spaces
                # Handle cases like "Math_Exams" -> "Math"
                subject_name = subject_name.replace('_', ' ').strip()
                
                # Normalize score_type for lookup
                normalized_st = st.replace('_', ' ')
                if 'Score' in normalized_st:
                    score_type = normalized_st.replace(' Score', ' Score')
                elif normalized_st in ['Grade', 'Remarks', 'Total Score']:
                    score_type = normalized_st
                else:
                    score_type = normalized_st
                break
            
            if subject_name and score_type:
                # CRITICAL: Get value using the ORIGINAL column name (col_str), not the processed one
                # This ensures we match the EXACT column name from the Google Sheet
                value = student_row.get(col_str)
                # Handle NaN and empty values properly
                if pd.notna(value) and str(value).strip() != '':
                    display_value = value
                else:
                    display_value = 'N/A'
                
                # Store with normalized subject name for display purposes
                if subject_name not in subjects_dict:
                    subjects_dict[subject_name] = {'Subject': subject_name}
                
                subjects_dict[subject_name][score_type] = display_value
                
                # DEBUG: Print what we're reading
                print(f"DEBUG READ: col_str='{col_str}' -> subject_name='{subject_name}', score_type='{score_type}', value='{display_value}'")
        
        print(f"DEBUG: {semester} subjects found: {list(subjects_dict.keys())}")
        
        # Get Grades and Remarks directly from the sheet (don't calculate)
        # The sheet already has columns like "Math Grade - 2025 Semester 1" and "Math Remarks - 2025 Semester 1"
        # Just read them as-is
        core_keywords_lower = ['math', 'science', 'social', 'english', 'ict', 'entrepreneurship', 'entrepreneur']
        
        for subj_name, scores in subjects_dict.items():
            exams = scores.get('Exams Score', 'N/A')
            class_score = scores.get('Class Score', 'N/A')
            
            # Calculate total from exams + class ONLY if Total Score is not already in sheet
            total_in_sheet = scores.get('Total Score', 'N/A')
            if total_in_sheet == 'N/A' and exams != 'N/A' and class_score != 'N/A':
                try:
                    total = float(exams) + float(class_score)
                    scores['Total Score'] = total
                except:
                    scores['Total Score'] = 'N/A'
            
            # Try to read Grade and Remarks from the sheet columns - handle both space and underscore formats
            grade_col_name = f"{subj_name} Grade - {semester}"
            remarks_col_name = f"{subj_name} Remarks - {semester}"
            
            # Also try underscore versions (in case sheet uses underscores)
            grade_col_name_underscore = f"{subj_name.replace(' ', '_')} Grade - {semester}"
            remarks_col_name_underscore = f"{subj_name.replace(' ', '_')} Remarks - {semester}"
            
            # Try to find Grade value - try multiple column name formats
            grade_from_sheet = 'N/A'
            for col_name in [grade_col_name, grade_col_name_underscore]:
                val = student_row.get(col_name)
                if pd.notna(val) and str(val).strip() != '' and str(val).strip().upper() != 'N/A':
                    grade_from_sheet = val
                    print(f"DEBUG: Found grade in column '{col_name}': '{grade_from_sheet}'")
                    break
            
            # Try to find Remarks value - try multiple column name formats
            remarks_from_sheet = 'N/A'
            for col_name in [remarks_col_name, remarks_col_name_underscore]:
                val = student_row.get(col_name)
                if pd.notna(val) and str(val).strip() != '' and str(val).strip().upper() != 'N/A':
                    remarks_from_sheet = val
                    print(f"DEBUG: Found remarks in column '{col_name}': '{remarks_from_sheet}'")
                    break
            
            # Handle NaN and empty values from sheet
            if pd.notna(grade_from_sheet) and str(grade_from_sheet).strip() != '' and str(grade_from_sheet).strip().upper() != 'N/A':
                scores['Grade'] = grade_from_sheet
            else:
                scores['Grade'] = 'N/A'
            
            if pd.notna(remarks_from_sheet) and str(remarks_from_sheet).strip() != '' and str(remarks_from_sheet).strip().upper() != 'N/A':
                scores['Remarks'] = remarks_from_sheet
            else:
                scores['Remarks'] = 'N/A'
            
            print(f"DEBUG: {subj_name} - Grade from sheet: {scores.get('Grade', 'N/A')}, Remarks from sheet: {scores.get('Remarks', 'N/A')}")
            
            # Classify as Core or Elective based on student's department
            subj_lower = subj_name.lower()
            is_core = any(keyword in subj_lower for keyword in core_keywords_lower)
            
            # SPECIAL CASE: "Practicals" is compulsory for ALL departments
            # It appears in EVERY department's list, so treat it separately
            is_practicals = 'practicals' in subj_lower
            
            # Check if this subject is an elective for this student's department ONLY
            # STRICT MATCH: Only add if exactly matches or very close match to department electives
            is_student_elective = False
            for elective in student_electives:
                # Skip 'praticals' for now - handled separately
                if elective.lower() == 'practicals':
                    continue
                # Exact match or close match (contains the key words)
                if subj_lower == elective:
                    is_student_elective = True
                    break
                # Check if subject name contains the elective or vice versa (for partial matches like "Electrical installation" vs "electrical installation")
                if elective in subj_lower or subj_lower in elective:
                    # Make sure it's not a partial word match (e.g., "math" should NOT match "mathematics")
                    elective_words = elective.split()
                    subj_words = subj_lower.split()
                    # All words in elective should be in subject
                    if all(word in subj_words for word in elective_words):
                        is_student_elective = True
                        break
            
            # Check if this subject exists as an elective in any OTHER department (excluding 'practicals')
            # If it does, SKIP it regardless
            is_other_dept_elective = False
            for dept_key, dept_electives in ELECTIVE_SUBJECT_NAMES_BY_DEPARTMENT.items():
                if dept_key.lower() != dept_lower:  # Skip student's own department
                    for dept_elective in dept_electives:
                        # Skip 'practicals' - it's in all departments but should be shown to all
                        if dept_elective.lower() == 'practicals':
                            continue
                        dept_elective_lower = dept_elective.lower()
                        # Only mark as other dept if there's a good match
                        if subj_lower == dept_elective_lower or dept_elective_lower in subj_lower:
                            is_other_dept_elective = True
                            print(f"DEBUG: Skipping '{subj_name}' - it's for {dept_key} department")
                            break
                    if is_other_dept_elective:
                        break
            
            if is_core:
                # Core subjects for all students
                semester_data['Core Subjects'].append(scores)
            elif is_practicals:
                # Practicals is compulsory for ALL departments - ALWAYS SHOW
                semester_data['Elective Subjects'].append(scores)
                print(f"DEBUG: Adding compulsory 'Practicals' for all students")
            elif is_student_elective and not is_other_dept_elective:
                # This is an elective ONLY for this student's department - ADD IT
                semester_data['Elective Subjects'].append(scores)
                print(f"DEBUG: Adding elective '{subj_name}' for {student_department} student")
            elif is_other_dept_elective:
                # This subject belongs to another department - SKIP IT (don't show)
                pass
            else:
                # Unknown subject that doesn't match any department - don't add to electives
                # This prevents showing random subjects
                print(f"DEBUG: Unknown subject '{subj_name}' - skipped")
        
        # Add semester if it has subjects
        if semester_data['Core Subjects'] or semester_data['Elective Subjects']:
            results_by_semester[semester] = semester_data
    
    print(f"DEBUG: Final results - semesters: {list(results_by_semester.keys())}")
    for sem, data in results_by_semester.items():
        print(f"DEBUG: {sem} - Core: {len(data.get('Core Subjects', []))}, Elective: {len(data.get('Elective Subjects', []))}")
    
    return {
        'student_info': student_info,
        'results_by_semester': results_by_semester
    }


@app.route('/admin')
def admin_dashboard():
    """Admin dashboard to view all results and trigger SMS (protected)."""
    # Check if admin is logged in
    if not session.get('admin_logged_in'):
        flash('Please log in to access the admin dashboard.', 'warning')
        return redirect(url_for('admin_login'))

    # Load data from Google Sheets (PRIMARY) with local Excel as fallback
    df = load_results()
    if df.empty:
        return render_template('admin.html', error="No student data available. Please sync from Google Sheets or add students locally.")

    # --- Search functionality for admin dashboard ---
    search_query = request.args.get('search_query')
    if search_query:
        search_query = search_query.strip()
        # Filter DataFrame by student name OR student ID (case-insensitive)
        name_matches = pd.Series([False] * len(df), index=df.index)
        id_matches = pd.Series([False] * len(df), index=df.index)
        
        if COLUMN_MAPPING.get('Student Name') and COLUMN_MAPPING['Student Name'] in df.columns:
            name_matches = df[COLUMN_MAPPING['Student Name']].astype(str).str.contains(search_query, case=False, na=False)
        
        if COLUMN_MAPPING.get('Student ID') and COLUMN_MAPPING['Student ID'] in df.columns:
            id_matches = df[COLUMN_MAPPING['Student ID']].astype(str).str.contains(search_query, case=False, na=False)
        
        # Combine name and ID matches
        df = df[name_matches | id_matches]
        
        if df.empty:
            flash(f'No students found matching "{search_query}".', 'info')

    # Prepare student_data for the admin.html template
    # This should only include the essential columns for the main dashboard table
    # Map new column names to expected template format
    student_data_for_template = []
    
    # Debug: Print column names and first row
    print(f"DEBUG: DataFrame columns: {list(df.columns)}")
    print(f"DEBUG: COLUMN_MAPPING: {COLUMN_MAPPING}")
    
    for index, row in df.iterrows():
        # Get student ID - try multiple possible column names
        student_id = None
        for col in df.columns:
            col_lower = col.strip().lower()
            if 'student id' in col_lower or col_lower == 'id' or col_lower == 'studentid':
                student_id = row[col]
                break
        if student_id is None:
            student_id = 'N/A'
        
        # Get student name - try multiple possible column names
        student_name = None
        for col in df.columns:
            col_lower = col.strip().lower()
            if 'student name' in col_lower or col_lower == 'name' or col_lower == 'studentname':
                student_name = row[col]
                break
        if student_name is None:
            student_name = 'N/A'
        
        # Get parent phone - try multiple possible column names
        parent_phone = None
        for col in df.columns:
            col_lower = col.strip().lower()
            if 'parent phone' in col_lower or 'phone' in col_lower or 'mobile' in col_lower:
                parent_phone = row[col]
                break
        if parent_phone is None:
            parent_phone = 'N/A'
        
        # Get department - try multiple possible column names
        department = None
        for col in df.columns:
            col_lower = col.strip().lower()
            if 'department' in col_lower or 'dept' in col_lower:
                department = row[col]
                break
        if department is None:
            department = 'N/A'
        
        # Debug: Print each student's data
        print(f"DEBUG Student: ID={student_id}, Name={student_name}, Dept={department}")
        
        student_data_for_template.append({
            'Student ID': str(student_id).strip() if student_id else 'N/A',
            'Student Name': str(student_name).strip() if student_name else 'N/A',
            'Parent Phone': str(parent_phone).strip() if parent_phone else 'N/A',
            'Student Department': str(department).strip() if department else 'N/A'
        })

    return render_template('admin.html', student_data=student_data_for_template, search_query=search_query)


@app.route('/admin/logout')
def admin_logout():
    """Logs out the admin user."""
    session.pop('admin_logged_in', None) # Remove session variable
    flash('Logged out successfully.', 'success')
    return redirect(url_for('admin_login'))


# --- HOD Authentication Routes ---
@app.route('/hod_login', methods=['GET', 'POST'])
def hod_login():
    """Handles HOD login via username and password."""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # Check if username exists and password matches
        if username in HOD_CREDENTIALS:
            hod_data = HOD_CREDENTIALS[username]
            if check_password_hash(hod_data['password'], password):
                session['hod_logged_in'] = True
                session['hod_username'] = username
                session['hod_department'] = hod_data['department']
                flash(f'Welcome, HOD of {hod_data["department"]}!', 'success')
                return redirect(url_for('hod_dashboard'))
            else:
                flash('Invalid password.', 'danger')
        else:
            flash('Invalid username.', 'danger')
        
        return render_template('hod_login.html')
    
    # Handle GET request - display HOD login form
    return render_template('hod_login.html')


@app.route('/hod/logout')
def hod_logout():
    """Logs out the HOD user."""
    session.pop('hod_logged_in', None)
    session.pop('hod_username', None)
    session.pop('hod_department', None)
    flash('Logged out successfully.', 'success')
    return redirect(url_for('hod_login'))


@app.route('/hod/dashboard')
def hod_dashboard():
    """HOD dashboard to view and add remarks for students in their department."""
    if not session.get('hod_logged_in'):
        flash('Please log in to access the HOD dashboard.', 'warning')
        return redirect(url_for('hod_login'))
    
    hod_department = session.get('hod_department')
    
    df = load_results_from_sheet()
    if df.empty:
        return render_template('hod_dashboard.html', students=[], department=hod_department, error="Could not load student data.")
    
    # Filter students by HOD's department
    if COLUMN_MAPPING.get('Student Department') and COLUMN_MAPPING['Student Department'] in df.columns:
        df = df[df[COLUMN_MAPPING['Student Department']].astype(str).str.lower() == hod_department.lower()]
    
    # Search functionality for HOD dashboard
    search_query = request.args.get('search_query')
    if search_query:
        search_query = search_query.strip()
        name_matches = pd.Series([False] * len(df), index=df.index)
        id_matches = pd.Series([False] * len(df), index=df.index)
        
        if COLUMN_MAPPING.get('Student Name') and COLUMN_MAPPING['Student Name'] in df.columns:
            name_matches = df[COLUMN_MAPPING['Student Name']].astype(str).str.contains(search_query, case=False, na=False)
        
        if COLUMN_MAPPING.get('Student ID') and COLUMN_MAPPING['Student ID'] in df.columns:
            id_matches = df[COLUMN_MAPPING['Student ID']].astype(str).str.contains(search_query, case=False, na=False)
        
        df = df[name_matches | id_matches]
        
        if df.empty:
            flash(f'No students found matching "{search_query}".', 'info')
    
    # Prepare student data for the template
    students_for_template = []
    for index, row in df.iterrows():
        student_info = {
            'Student ID': row.get(COLUMN_MAPPING.get('Student ID'), 'N/A'),
            'Student Name': row.get(COLUMN_MAPPING.get('Student Name'), 'N/A'),
            'Student Department': row.get(COLUMN_MAPPING.get('Student Department'), 'N/A'),
        }
        students_for_template.append(student_info)
    
    return render_template('hod_dashboard.html', 
                           students=students_for_template, 
                           department=hod_department,
                           search_query=search_query)


@app.route('/hod/student/<student_id>/remarks', methods=['GET', 'POST'])
def hod_student_remarks(student_id):
    """Allows HOD to view and add remarks for a specific student."""
    if not session.get('hod_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('hod_login'))
    
    hod_department = session.get('hod_department')
    
    # Get student data
    student_full_data = get_all_student_results_by_id(student_id)
    
    if not student_full_data['student_info']:
        flash(f"Student with ID {student_id} not found.", 'danger')
        return redirect(url_for('hod_dashboard'))
    
    # Verify the student belongs to HOD's department
    student_department = student_full_data['student_info'].get('Student Department', '')
    if student_department.lower() != hod_department.lower():
        flash('You can only add remarks for students in your department.', 'danger')
        return redirect(url_for('hod_dashboard'))
    
    if request.method == 'POST':
        remarks = request.form.get('remarks')
        
        if not remarks or not remarks.strip():
            flash('Remarks cannot be empty.', 'warning')
            return redirect(url_for('hod_student_remarks', student_id=student_id))
        
        # Update Google Sheet with HOD remarks
        gc = get_google_sheet_client()
        if not gc:
            flash('Could not connect to Google Sheets. Please try again later.', 'danger')
            return redirect(url_for('hod_student_remarks', student_id=student_id))
        
        try:
            sh = gc.open_by_key(UNIFIED_GOOGLE_SHEET_ID)
            worksheet = sh.sheet1
            all_records = worksheet.get_all_records()
            
            if all_records:
                sheet_headers = list(all_records[0].keys())
                
                # Check if 'HOD Remarks' column exists, if not, add it
                hod_remarks_col = 'HOD Remarks'
                if hod_remarks_col not in sheet_headers:
                    # Add new column for HOD Remarks
                    next_col_idx = len(sheet_headers) + 1
                    worksheet.update_cell(1, next_col_idx, hod_remarks_col)
                    sheet_headers.append(hod_remarks_col)
                    print(f"Added new column '{hod_remarks_col}' to Google Sheet")
                
                # Find the student row and update remarks
                student_id_col = COLUMN_MAPPING.get('Student ID')
                if student_id_col and student_id_col in sheet_headers:
                    student_id_idx = sheet_headers.index(student_id_col) + 1
                    hod_remarks_idx = sheet_headers.index(hod_remarks_col) + 1
                    
                    for idx, record in enumerate(all_records):
                        if str(record.get(student_id_col, '')).strip() == student_id.strip():
                            # Update the HOD Remarks cell
                            worksheet.update_cell(idx + 2, hod_remarks_idx, remarks)
                            flash('Remarks saved successfully!', 'success')
                            break
                    else:
                        flash('Student not found in Google Sheet.', 'danger')
                else:
                    flash('Student ID column not found in sheet.', 'danger')
            else:
                flash('No records found in Google Sheet.', 'danger')
                
        except Exception as e:
            flash(f'Error updating Google Sheet: {e}', 'danger')
            print(f"Error updating HOD remarks: {e}")
        
        return redirect(url_for('hod_student_remarks', student_id=student_id))
    
    # GET request - show the remarks form
    # Get existing remarks from Google Sheet
    existing_remarks = ''
    try:
        gc = get_google_sheet_client()
        if gc:
            sh = gc.open_by_key(UNIFIED_GOOGLE_SHEET_ID)
            worksheet = sh.sheet1
            all_records = worksheet.get_all_records()
            
            if all_records:
                sheet_headers = list(all_records[0].keys())
                student_id_col = COLUMN_MAPPING.get('Student ID')
                
                if student_id_col and student_id_col in sheet_headers:
                    student_id_idx = sheet_headers.index(student_id_col) + 1
                    
                    for idx, record in enumerate(all_records):
                        if str(record.get(student_id_col, '')).strip() == student_id.strip():
                            # Check for HOD Remarks column
                            if 'HOD Remarks' in sheet_headers:
                                hod_remarks_idx = sheet_headers.index('HOD Remarks') + 1
                                existing_remarks = record.get('HOD Remarks', '')
                            break
    except Exception as e:
        print(f"Error fetching existing remarks: {e}")
    
    return render_template('hod_student_remarks.html', 
                           student_data=student_full_data,
                           existing_remarks=existing_remarks)


# --- Admin Route to Manage HOD Accounts ---
@app.route('/admin/manage_hods', methods=['GET', 'POST'])
def manage_hods():
    """Allows admin to manage HOD accounts."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'add':
            username = request.form.get('username')
            password = request.form.get('password')
            department = request.form.get('department')
            
            if not username or not password or not department:
                flash('Please fill in all fields.', 'warning')
            else:
                # Check if username already exists in file
                file_path = get_excel_path('staff_accounts')
                if os.path.exists(file_path):
                    df = pd.read_excel(file_path)
                    existing = df[df['username'].str.strip() == username.strip()]
                    if not existing.empty:
                        flash('Username already exists.', 'danger')
                        return render_template('manage_hods.html', 
                                               hods=get_hod_list_from_file(),
                                               available_departments=AVAILABLE_DEPARTMENTS)
                
                # Add HOD (saves to Excel and syncs to Google Sheet)
                result = add_hod(username, password, department)
                if result:
                    flash(f'HOD account for {department} created successfully!', 'success')
                else:
                    flash(f'Failed to create HOD account: {result}', 'danger')
        
        elif action == 'delete':
            username = request.form.get('username')
            file_path = get_excel_path('staff_accounts')
            
            if os.path.exists(file_path):
                try:
                    df = pd.read_excel(file_path)
                    # Remove the HOD account
                    df = df[~((df['username'].str.strip() == username) & (df['role'].str.strip().str.lower() == 'hod'))]
                    df.to_excel(file_path, index=False)
                    
                    # Also remove from in-memory dict
                    if username in HOD_CREDENTIALS:
                        del HOD_CREDENTIALS[username]
                    
                    # Sync to Google Sheets
                    try:
                        push_data_to_unified_sheet('staff_accounts')
                    except:
                        pass
                    
                    flash(f'HOD account "{username}" deleted successfully.', 'success')
                except Exception as e:
                    flash(f'Failed to delete HOD account: {str(e)}', 'danger')
            else:
                flash('Staff database file not found.', 'danger')
    
    # Get list of HOD accounts from file
    hod_list = get_hod_list_from_file()
    
    return render_template('manage_hods.html', 
                           hods=hod_list,
                           available_departments=AVAILABLE_DEPARTMENTS)


def get_hod_list_from_file():
    """Get list of HOD accounts from staff_users.xlsx file."""
    hod_list = []
    file_path = get_excel_path('staff_accounts')
    
    if os.path.exists(file_path):
        try:
            df = pd.read_excel(file_path)
            hod_df = df[df['role'].str.strip().str.lower() == 'hod']
            for _, row in hod_df.iterrows():
                hod_list.append({
                    'username': row['username'],
                    'department': row.get('department', '')
                })
        except Exception as e:
            print(f"Error loading HOD list from file: {e}")
    
    return hod_list


# --- Admin Route to Manage Instructor Accounts ---
@app.route('/admin/manage_instructors', methods=['GET', 'POST'])
def admin_manage_instructors():
    """Allows admin to view, add, edit, and delete instructor accounts."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Define core subjects available to all instructors
    CORE_SUBJECTS = ['Math', 'English', 'Science', 'Social', 'ICT', 'Entrepreneur']
    
    # Define elective subjects per department (matching ELECTIVE_SUBJECT_NAMES_BY_DEPARTMENT)
    ELECTIVE_SUBJECTS_BY_DEPT = {
        'General Science': ['Physics', 'Chemistry', 'Biology', 'Elective Maths'],
        'General Arts': ['History', 'Literature', 'Government'],
        'Business': ['Accounting', 'Economics', 'Business Management', 'Elective Maths'],
        'Home Science': ['Food & Nutrition', 'Textiles', 'Management in Living', 'Chemistry'],
        'General Agric': ['General Agriculture', 'Crop Husbandry', 'Animal Husbandry', 'Chemistry', 'Elective Maths'],
        'Visual Arts': ['Picture Making', 'Graphic Design', 'Pottery', 'Textiles'],
        'Hospitality': ['Food Preparation', 'Hotel Operations', 'Front Office', 'Housekeeping'],
        'clothing': ['Garment Construction', 'Textiles & Fashion Design', 'Laundry Operations'],
        'electricals': ['Electrical Principles', 'Electrical Installation', 'Testing & Commissioning', 'practicals'],
        'building': ['Building Drawing', 'Building Technology', 'Materials Technology', 'practicals'],
        'wood': ['Wood Principles', 'Wood Technology', 'practicals']
    }
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'add':
            name = request.form.get('name')
            username = request.form.get('username')
            password = request.form.get('password')
            department = request.form.get('department', '')
            assigned_subjects = request.form.getlist('assigned_subjects')
            
            if not name or not username or not password:
                flash('Please fill in all required fields (Name, Username, Password).', 'warning')
            elif not assigned_subjects:
                flash('Please select at least one subject for the instructor.', 'warning')
            else:
                # Check if username already exists
                df = pd.read_excel(get_excel_path('instructors'))
                if not df.empty and username in df['username'].values:
                    flash(f'Username "{username}" already exists. Please choose a different username.', 'danger')
                else:
                    # Create new instructor
                    instructor_id = f"INST-{len(df) + 1:04d}" if not df.empty else "INST-0001"
                    password_hash = generate_password_hash(password)
                    subjects_str = ','.join(assigned_subjects)
                    
                    new_record = pd.DataFrame([{
                        'id': len(df) + 1 if not df.empty else 1,
                        'instructor_id': instructor_id,
                        'name': name,
                        'username': username,
                        'password_hash': password_hash,
                        'assigned_subjects': subjects_str,
                        'department': department,
                        'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    }])
                    
                    df = pd.concat([df, new_record], ignore_index=True)
                    df.to_excel(get_excel_path('instructors'), index=False)
                    
                    # Sync to Google Sheet
                    save_instructors_to_sheet()
                    
                    flash(f'Instructor "{name}" added successfully with subjects: {subjects_str}', 'success')
        
        elif action == 'delete':
            instructor_id = request.form.get('instructor_id')
            df = pd.read_excel(get_excel_path('instructors'))
            if not df.empty and instructor_id in df['instructor_id'].values:
                df = df[df['instructor_id'] != instructor_id]
                df.to_excel(get_excel_path('instructors'), index=False)
                
                # Sync to Google Sheet
                save_instructors_to_sheet()
                
                flash(f'Instructor "{instructor_id}" deleted successfully.', 'success')
            else:
                flash('Instructor not found.', 'danger')
        
        elif action == 'edit':
            instructor_id = request.form.get('instructor_id')
            name = request.form.get('name')
            username = request.form.get('username')
            department = request.form.get('department', '')
            assigned_subjects = request.form.getlist('assigned_subjects')
            
            df = pd.read_excel(get_excel_path('instructors'))
            if not df.empty and instructor_id in df['instructor_id'].values:
                idx = df[df['instructor_id'] == instructor_id].index[0]
                df.at[idx, 'name'] = name
                df.at[idx, 'username'] = username
                df.at[idx, 'department'] = department
                df.at[idx, 'assigned_subjects'] = ','.join(assigned_subjects)
                df.to_excel(get_excel_path('instructors'), index=False)
                
                # Sync to Google Sheet
                save_instructors_to_sheet()
                
                flash(f'Instructor "{name}" updated successfully.', 'success')
            else:
                flash('Instructor not found.', 'danger')
    
    # Load all instructors
    df = pd.read_excel(get_excel_path('instructors'))
    instructors = df.to_dict('records') if not df.empty else []
    
    return render_template('admin_manage_instructors.html',
                           instructors=instructors,
                           core_subjects=CORE_SUBJECTS,
                           elective_subjects_by_dept=ELECTIVE_SUBJECTS_BY_DEPT)


# --- Admin SMS Actions Page Route ---
@app.route('/admin/sms_actions', methods=['GET'])
def admin_sms_actions():
    """Admin page to select SMS recipient group."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    return render_template('admin_sms_actions.html')


# --- Send SMS to All Instructors Route ---
@app.route('/admin/send_sms_to_instructors', methods=['GET', 'POST'])
def admin_send_sms_to_instructors():
    """Allows admin to send SMS to all instructors."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        
        if not message:
            flash('Please enter a message to send.', 'warning')
            return redirect(url_for('admin_send_sms_to_instructors'))
        
        # Load instructors
        df = pd.read_excel(get_excel_path('instructors'))
        
        if df.empty:
            flash('No instructors found to send SMS.', 'info')
            return redirect(url_for('admin_dashboard'))
        
        sent_count = 0
        failed_count = 0
        
        for _, instructor in df.iterrows():
            # Check if instructor has a phone number
            phone = instructor.get('phone', '')
            if phone and str(phone).strip():
                success, result = send_sms(str(phone), message)
                if success:
                    sent_count += 1
                else:
                    failed_count += 1
            else:
                failed_count += 1
        
        flash(f'SMS sent to {sent_count} instructors. Failed: {failed_count}', 'info')
        return redirect(url_for('admin_dashboard'))
    
    return render_template('admin_send_sms_to_instructors.html')


# --- Send SMS to All HODs Route ---
@app.route('/admin/send_sms_to_hods', methods=['GET', 'POST'])
def admin_send_sms_to_hods():
    """Allows admin to send SMS to all HODs."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        
        if not message:
            flash('Please enter a message to send.', 'warning')
            return redirect(url_for('admin_send_sms_to_hods'))
        
        # Load HODs from staff_users.xlsx
        file_path = get_excel_path('staff_accounts')
        
        if not os.path.exists(file_path):
            flash('No staff accounts found.', 'info')
            return redirect(url_for('admin_dashboard'))
        
        df = pd.read_excel(file_path)
        hod_df = df[df['role'].str.strip().str.lower() == 'hod']
        
        if hod_df.empty:
            flash('No HODs found to send SMS.', 'info')
            return redirect(url_for('admin_dashboard'))
        
        sent_count = 0
        failed_count = 0
        
        for _, hod in hod_df.iterrows():
            phone = hod.get('phone', '')
            if phone and str(phone).strip():
                success, result = send_sms(str(phone), message)
                if success:
                    sent_count += 1
                else:
                    failed_count += 1
            else:
                failed_count += 1
        
        flash(f'SMS sent to {sent_count} HODs. Failed: {failed_count}', 'info')
        return redirect(url_for('admin_dashboard'))
    
    return render_template('admin_send_sms_to_hods.html')


# --- Instructor Login Route ---
@app.route('/instructor_login', methods=['GET', 'POST'])
def instructor_login():
    """Allows instructors to log in to their dashboard."""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        df = pd.read_excel(get_excel_path('instructors'))
        if not df.empty:
            instructor = df[df['username'] == username]
            if not instructor.empty:
                stored_hash = instructor.iloc[0]['password_hash']
                if check_password_hash(stored_hash, password):
                    session['instructor_logged_in'] = True
                    session['instructor_id'] = instructor.iloc[0]['instructor_id']
                    session['instructor_name'] = instructor.iloc[0]['name']
                    session['instructor_subjects'] = instructor.iloc[0]['assigned_subjects'].split(',')
                    # Store instructor's department for filtering students
                    session['instructor_department'] = instructor.iloc[0].get('department', '')
                    flash(f'Welcome, {instructor.iloc[0]["name"]}!', 'success')
                    return redirect(url_for('instructor_dashboard'))
                else:
                    flash('Invalid password. Please try again.', 'danger')
            else:
                flash('Username not found. Please check your credentials.', 'danger')
        else:
            flash('No instructors registered yet. Please contact the administrator.', 'warning')
    
    return render_template('instructor_login.html')


# --- Instructor Dashboard Route ---
# --- Instructor Dashboard Route ---
@app.route('/instructor/dashboard')
def instructor_dashboard():
    """Instructor dashboard to view and enter results for assigned subjects."""
    if not session.get('instructor_logged_in'):
        flash('Please log in to access the instructor dashboard.', 'warning')
        return redirect(url_for('instructor_login'))
    
    instructor_subjects = session.get('instructor_subjects', [])
    instructor_name = session.get('instructor_name', 'Instructor')
    instructor_department = session.get('instructor_department', '')
    primary_subject = instructor_subjects[0] if instructor_subjects else ''
    
    # Load students data from Google Sheets
    df_students = load_results_from_sheet()
    
    if df_students.empty:
        # Fallback to local Excel if Google Sheets is empty
        try:
            df_students = pd.read_excel(get_excel_path('students'))
        except:
            df_students = pd.DataFrame()
    
    # Normalize column names for consistent access
    students = []
    all_student_results = {}
    
    if not df_students.empty:
        # Build a set of subjects for the instructor's department (for filtering)
        dept_subjects_for_instructor = set()
        if instructor_department and instructor_department != 'all':
            # Get the elective subjects for the instructor's department
            dept_subjects_for_instructor = set(ELECTIVE_SUBJECT_NAMES_BY_DEPARTMENT.get(instructor_department, []))
        
        for idx, row in df_students.iterrows():
            student_record = {}
            # Normalize Student ID
            student_record['Student ID'] = row.get('Student ID', row.get('StudentID', row.get('student_id', '')))
            # Normalize Student Name
            student_record['Student Name'] = row.get('Student Name', row.get('StudentName', row.get('student_name', '')))
            # Normalize Department - try multiple possible column names
            student_department = row.get('Student Department', row.get('Department', row.get('department', row.get('Class', ''))))
            student_record['Department'] = student_department
            
            # FILTERING LOGIC: Only show students who match the instructor's department
            # Elective subject instructors ONLY see students from their EXACT department
            # Core subject instructors (Math, English, etc.) see ALL students
            
            is_core_subject_instructor = any(subj in CORE_SUBJECT_NAMES for subj in instructor_subjects)
            
            should_include_student = False
            
            if is_core_subject_instructor:
                # Core subject instructors see ALL students
                should_include_student = True
            elif instructor_department and instructor_department != 'all':
                # Strict department matching for elective subject instructors
                student_dept_lower = str(student_department).lower().strip()
                instr_dept_lower = instructor_department.lower().strip()
                
                # ONLY include students from EXACT department match (no fallback)
                if student_dept_lower == instr_dept_lower:
                    should_include_student = True
            
            if not should_include_student:
                continue
            
            students.append(student_record)
            
            # Get previous results for this student for instructor subjects
            student_id = student_record['Student ID']
            student_results_by_year = {}
            
            # Check results for each instructor subject
            for subj in instructor_subjects:
                # Look for all semester columns
                for col in df_students.columns:
                    if col.startswith(subj) and 'Exams Score' in col:
                        parts = col.rsplit(' - ', 1)
                        if len(parts) >= 1:
                            semester_part = parts[-1] if len(parts) > 1 else ''
                            acad_year = semester_part.split(' ')[0] if semester_part else ''
                            sem_name = ' '.join(semester_part.split(' ')[1:]) if semester_part else ''
                            
                            # Build column names
                            class_col = col.replace('Exams Score', 'Class Score')
                            total_col = col.replace('Exams Score', 'Total Score')
                            grade_col = col.replace('Exams Score', 'Grade')
                            remarks_col = col.replace('Exams Score', 'Remarks')
                            
                            # Check if this result is for the instructor's subject
                            subject_from_col = col.replace(' Exams Score - ' + semester_part, '')

                            if subject_from_col == subj:
                                class_score = row.get(class_col, '')
                                exam_score = row.get(col, '')
                                total_score = row.get(total_col, '')
                                grade = row.get(grade_col, '')
                                remarks = row.get(remarks_col, '')
                                
                                # Build nested structure for JavaScript
                                if acad_year not in student_results_by_year:
                                    student_results_by_year[acad_year] = {}
                                
                                # Get exam/class score for display (combined score)
                                score_val = ''
                                if total_score and str(total_score).strip():
                                    score_val = str(total_score)
                                elif class_score and exam_score:
                                    try:
                                        total = float(class_score) + float(exam_score)
                                        score_val = str(total)
                                    except:
                                        score_val = str(class_score) if class_score else 'N/A'
                                
                                student_results_by_year[acad_year][sem_name] = {
                                    'score': score_val if score_val else 'N/A',
                                    'grade': str(grade) if grade and str(grade).strip() else 'N/A',
                                    'remarks': str(remarks) if remarks and str(remarks).strip() else '-'
                                }
            
            all_student_results[str(student_id)] = student_results_by_year
    
    # Load result entry settings
    result_settings = None
    df_settings = load_excel_data('settings')
    if not df_settings.empty:
        is_active_row = df_settings[df_settings['key'] == 'result_is_active']
        deadline_row = df_settings[df_settings['key'] == 'result_deadline']
        if not is_active_row.empty:
            result_settings = type('obj', (object,), {
                'is_active': bool(int(is_active_row['value'].values[0])),
                'deadline': deadline_row['value'].values[0] if not deadline_row.empty else ''
            })()
    
    # Dynamically generate years from 2020 to 2050
    years = [str(year) for year in range(2020, 2051)]
    
    return render_template('instructor_dashboard.html',
                           instructor_name=instructor_name,
                           instructor_subjects=instructor_subjects,
                           primary_subject=primary_subject,
                           students=students,
                           all_student_results=all_student_results,
                           result_settings=result_settings,
                           years=years)
# --- Instructor Enter Results Route ---
@app.route('/instructor/enter_results', methods=['GET', 'POST'])
def instructor_enter_results():
    """Allows instructors to enter results for their assigned subjects."""
    if not session.get('instructor_logged_in'):
        flash('Please log in to access the instructor dashboard.', 'warning')
        return redirect(url_for('instructor_login'))
    
    instructor_subjects = session.get('instructor_subjects', [])
    
    if request.method == 'POST':
        student_id = request.form.get('student_id')
        subject = request.form.get('subject')
        exam_score = request.form.get('exam_score')
        class_score = request.form.get('class_score')
        semester = request.form.get('semester')
        academic_year = request.form.get('academic_year')
        
        # Verify the subject is assigned to this instructor
        if subject not in instructor_subjects:
            flash('You are not authorized to enter results for this subject.', 'danger')
            return redirect(url_for('instructor_dashboard'))
        
        if not student_id or not subject or not exam_score or not class_score or not semester or not academic_year:
            flash('Please fill in all required fields.', 'warning')
            return redirect(url_for('instructor_dashboard'))
        
        try:
            # Load current data from Google Sheet
            df = load_results_from_sheet()
            
            if df.empty:
                flash('No student data found. Please upload student data first.', 'danger')
                return redirect(url_for('instructor_dashboard'))
            
            # Find the student
            student_rows = df[df['Student ID'].astype(str) == str(student_id)]
            
            if student_rows.empty:
                flash(f'Student ID "{student_id}" not found!', 'danger')
                return redirect(url_for('instructor_dashboard'))
            
            idx = student_rows.index[0]
            
            # Create semester suffix like " - 2026 Semester 1"
            semester_suffix = f" - {academic_year} {semester}"
            
            # Construct column names WITH semester suffix
            col_exam = f"{subject} Exams Score{semester_suffix}"
            col_class = f"{subject} Class Score{semester_suffix}"
            col_total = f"{subject} Total Score{semester_suffix}"
            col_grade = f"{subject} Grade{semester_suffix}"
            col_remarks = f"{subject} Remarks{semester_suffix}"
            
            # Add new columns if they don't exist
            for col in [col_exam, col_class, col_total, col_grade, col_remarks]:
                if col not in df.columns:
                    df[col] = ''
            
            # Convert scores to float
            exam_val = float(exam_score)
            class_val = float(class_score)
            
            # Calculate total as simple sum (Class + Exams)
            total_val = class_val + exam_val
            
            # GRADING SCALE
            if total_val >= 75:
                grade = 'A'
                remarks = 'DISTINCTION'
            elif total_val >= 70:
                grade = 'B+'
                remarks = 'VERY GOOD'
            elif total_val >= 65:
                grade = 'B'
                remarks = 'UPPER CREDIT'
            elif total_val >= 60:
                grade = 'C+'
                remarks = 'CREDIT'
            elif total_val >= 55:
                grade = 'C'
                remarks = 'LOWER CREDIT'
            elif total_val >= 45:
                grade = 'D'
                remarks = 'PASS'
            else:
                grade = 'F'
                remarks = 'FAIL'
            
            # Update DataFrame
            df.loc[idx, col_exam] = exam_val
            df.loc[idx, col_class] = class_val
            df.loc[idx, col_total] = total_val
            df.loc[idx, col_grade] = grade
            df.loc[idx, col_remarks] = remarks
            
            # Save to Google Sheet
            success = save_results_to_sheet_fix(df)
            
            # DEBUG: Reload to verify save
            if success:
                print(f"[DEBUG] Results saved to Google Sheet successfully")
                df_check = load_results_from_sheet()
                if not df_check.empty:
                    col_check = f"{subject} Total Score - {academic_year} {semester}"
                    if col_check in df_check.columns:
                        saved_val = df_check[df_check['Student ID'].astype(str) == str(student_id)][col_check].values
                        print(f"[DEBUG] Verification - saved value for {student_id}: {saved_val}")
                flash(f'Results for {student_id} in {subject} for {academic_year} {semester} saved successfully! Grade: {grade} ({remarks})', 'success')
            else:
                flash(f'Results saved locally. Google Sheet sync may be pending.', 'success')
                
        except Exception as e:
            flash(f'Error saving results: {str(e)}', 'danger')
            print(f"ERROR saving instructor results: {str(e)}")
    
    return redirect(url_for('instructor_dashboard'))



# --- Instructor Logout Route ---
@app.route('/instructor/logout')
def instructor_logout():
    """Logs out the instructor and clears the session."""
    session.pop('instructor_logged_in', None)
    session.pop('instructor_id', None)
    session.pop('instructor_name', None)
    session.pop('instructor_subjects', None)
    session.pop('instructor_department', None)
    flash('You have been logged out successfully.', 'info')
    return redirect(url_for('instructor_login'))


# NEW ROUTE: View a student's full results
@app.route('/admin/student/<student_id>/full_results')
def view_student_full_results(student_id):
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))

    student_full_data = get_all_student_results_by_id(student_id)

    if not student_full_data['student_info']:
        flash(f"Student with ID {student_id} not found or no data available.", 'danger')
        return redirect(url_for('admin_dashboard'))
    
    # Pass the full student data to the new template
    return render_template('view_student_full_results.html', student_data=student_full_data)
"""
Student Results Management API Routes
Handles CRUD operations for student results with online/offline sync.
"""

@app.route('/admin/students/manage')
def admin_manage_students():
    """Manage all students - List view with CRUD actions."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Load students from Google Sheet (online mode)
    df = load_results_from_sheet()
    
    if df.empty:
        flash('Unable to load student data. Please check your Google Sheet connection.', 'warning')
        students = []
    else:
        students = df.to_dict('records')
    
    return render_template('admin_students_manage.html', 
                           students=students,
                           AVAILABLE_DEPARTMENTS=AVAILABLE_DEPARTMENTS)


@app.route('/admin/student/add', methods=['GET', 'POST'])
def admin_add_student():
    """Add a new student to the database with AUTO-GENERATED ID."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    if request.method == 'POST':
        student_name = request.form.get('student_name', '').strip()
        father_name = request.form.get('father_name', '').strip()
        parent_phone = request.form.get('parent_phone', '').strip()
        department = request.form.get('department', '').strip()
        entry_year = request.form.get('entry_year', str(datetime.now().year)).strip()
        
        if not student_name or not department:
            flash('Student Name and Department are required.', 'warning')
            return redirect(url_for('admin_add_student'))
        
        try:
            # AUTO-GENERATE student ID based on department and year
            student_id = generate_student_id(department, int(entry_year))
            
            # Load existing data
            df = load_results_from_sheet()
            
            # Check if student ID already exists (safety check)
            if not df.empty and student_id in df['Student ID'].astype(str).values:
                flash(f'Generated ID {student_id} already exists. Please try again.', 'danger')
                return redirect(url_for('admin_add_student'))
            
            # Prepare new student row with all required columns
            new_student = {
                'Student ID': student_id,
                'Student Name': student_name,
                'Student Department': department,
                'Parent Phone': parent_phone,
            }
            
            # Add empty score columns for all subjects/semesters
            for col in df.columns:
                if col not in ['Student ID', 'Student Name', 'Student Department', 'Parent Phone']:
                    new_student[col] = ''
            
            # Add to DataFrame
            df = pd.concat([df, pd.DataFrame([new_student])], ignore_index=True)
            
            # Save to Google Sheet
            save_results_to_sheet(df)
            
            flash(f'Student "{student_name}" added successfully! Auto-generated ID: {student_id}', 'success')
            return redirect(url_for('admin_manage_students'))
            
        except Exception as e:
            flash(f'Error adding student: {str(e)}', 'danger')
    
    return render_template('admin_student_add.html',
                           AVAILABLE_DEPARTMENTS=AVAILABLE_DEPARTMENTS,
                           current_year=datetime.now().year)


@app.route('/admin/student/bulk_upload', methods=['GET', 'POST'])
def admin_bulk_upload_students():
    """Bulk upload students from Excel file with auto-generated IDs."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file selected. Please choose an Excel file.', 'danger')
            return redirect(url_for('admin_bulk_upload_students'))
        
        file = request.files['file']
        if file.filename == '':
            flash('No file selected. Please choose an Excel file.', 'danger')
            return redirect(url_for('admin_bulk_upload_students'))
        
        try:
            # Read the uploaded Excel file
            df_upload = pd.read_excel(file)
            
            # Expected columns: Student Name, Student Department, Parent Phone (optional)
            required_cols = ['Student Name', 'Student Department']
            missing_cols = [col for col in required_cols if col not in df_upload.columns]
            
            if missing_cols:
                flash(f'Missing required columns: {", ".join(missing_cols)}. Please use columns: Student Name, Student Department, Parent Phone (optional)', 'danger')
                return redirect(url_for('admin_bulk_upload_students'))
            
            # Load existing data from Google Sheet
            df_existing = load_results_from_sheet()
            
            # Get entry year from form (default to current year)
            entry_year = request.form.get('entry_year', str(datetime.now().year))
            
            added_count = 0
            skipped_count = 0
            errors = []
            
            # Process each row in the upload file
            for idx, row in df_upload.iterrows():
                try:
                    student_name = str(row.get('Student Name', '')).strip()
                    department = str(row.get('Student Department', '')).strip()
                    parent_phone = str(row.get('Parent Phone', row.get('Parent Phone', ''))).strip()
                    
                    # Skip empty rows
                    if not student_name or not department or student_name == 'nan' or department == 'nan':
                        continue
                    
                    # Auto-generate student ID
                    student_id = generate_student_id(department, int(entry_year))
                    
                    # Check for duplicate (in existing data)
                    if not df_existing.empty and student_id in df_existing['Student ID'].astype(str).values:
                        skipped_count += 1
                        continue
                    
                    # Prepare new student row
                    new_student = {
                        'Student ID': student_id,
                        'Student Name': student_name,
                        'Student Department': department,
                        'Parent Phone': parent_phone if parent_phone and parent_phone != 'nan' else '',
                    }
                    
                    # Add empty score columns
                    for col in df_existing.columns:
                        if col not in ['Student ID', 'Student Name', 'Student Department', 'Parent Phone']:
                            new_student[col] = ''
                    
                    # Add to DataFrame
                    df_existing = pd.concat([df_existing, pd.DataFrame([new_student])], ignore_index=True)
                    added_count += 1
                    
                except Exception as e:
                    errors.append(f"Row {idx + 1}: {str(e)}")
                    skipped_count += 1
            
            # Save to Google Sheet
            save_results_to_sheet(df_existing)
            
            # Also save to local Excel
            try:
                df_local = load_excel_data('students')
                # Add all new students to local Excel
                for idx, row in df_upload.iterrows():
                    try:
                        student_name = str(row.get('Student Name', '')).strip()
                        department = str(row.get('Student Department', '')).strip()
                        parent_phone = str(row.get('Parent Phone', '')).strip()
                        
                        if not student_name or not department or student_name == 'nan' or department == 'nan':
                            continue
                        
                        student_id = generate_student_id(department, int(entry_year))
                        
                        new_student_local = {
                            'id': 1 if df_local.empty else int(df_local['id'].max()) + 1,
                            'student_id': student_id,
                            'student_name': student_name,
                            'department': department,
                            'parent_phone': parent_phone if parent_phone and parent_phone != 'nan' else '',
                            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M')
                        }
                        
                        df_local = pd.concat([df_local, pd.DataFrame([new_student_local])], ignore_index=True)
                    except:
                        pass
                
                save_excel_data('students', df_local)
            except Exception as e:
                print(f"Error saving to local Excel: {e}")
            
            # Show results
            message = f'Bulk upload complete! Added: {added_count} students. Skipped: {skipped_count}'
            if errors:
                message += f' Errors: {len(errors)}'
            
            flash(message, 'success' if added_count > 0 else 'warning')
            return redirect(url_for('admin_manage_students'))
            
        except Exception as e:
            flash(f'Error processing file: {str(e)}', 'danger')
    
    return render_template('admin_bulk_upload_students.html',
                           current_year=datetime.now().year)


@app.route('/admin/student/<student_id>/edit', methods=['GET', 'POST'])
def admin_edit_student(student_id):
    """Edit an existing student's information."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    df = load_results_from_sheet()
    
    if df.empty:
        flash('Unable to load student data.', 'danger')
        return redirect(url_for('admin_manage_students'))
    
    # Find the student
    student_rows = df[df['Student ID'].astype(str) == str(student_id)]
    
    if student_rows.empty:
        flash(f'Student with ID {student_id} not found.', 'danger')
        return redirect(url_for('admin_manage_students'))
    
    student = student_rows.iloc[0].to_dict()
    
    if request.method == 'POST':
        student_name = request.form.get('student_name', '').strip()
        father_name = request.form.get('father_name', '').strip()
        parent_phone = request.form.get('parent_phone', '').strip()
        department = request.form.get('department', '').strip()
        
        if not student_name or not department:
            flash('Student Name and Department are required.', 'warning')
            return redirect(url_for('admin_edit_student', student_id=student_id))
        
        try:
            # Update the student's information
            idx = df[df['Student ID'].astype(str) == str(student_id)].index[0]
            df.loc[idx, 'Student Name'] = student_name
            df.loc[idx, 'Parent Phone'] = parent_phone
            df.loc[idx, 'Student Department'] = department
            
            # Save back to Google Sheet
            save_results_to_sheet(df)
            
            flash(f'Student "{student_name}" updated successfully!', 'success')
            return redirect(url_for('admin_manage_students'))
            
        except Exception as e:
            flash(f'Error updating student: {str(e)}', 'danger')
    
    return render_template('admin_student_edit.html',
                           student=student,
                           AVAILABLE_DEPARTMENTS=AVAILABLE_DEPARTMENTS)


@app.route('/admin/student/<student_id>/delete', methods=['POST'])
def admin_delete_student(student_id):
    """Delete a student from the database."""
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Not authorized'}), 401
    
    try:
        df = load_results_from_sheet()
        
        if df.empty:
            return jsonify({'success': False, 'message': 'No data to process'}), 400
        
        # Find and remove the student
        initial_count = len(df)
        df = df[df['Student ID'].astype(str) != str(student_id)]
        
        if len(df) == initial_count:
            return jsonify({'success': False, 'message': 'Student not found'}), 404
        
        # Save back to Google Sheet
        save_results_to_sheet(df)
        
        return jsonify({'success': True, 'message': f'Student {student_id} deleted successfully'})
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/admin/student/<student_id>/results/edit', methods=['GET', 'POST'])
def admin_edit_student_results(student_id):
    """Edit a student's results - Saves to Google Sheet"""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Load data from Google Sheet
    df = load_results_from_sheet()
    
    if df.empty:
        flash('Unable to load student data from Google Sheet.', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    # Find the student
    student_rows = df[df['Student ID'].astype(str) == str(student_id).strip()]
    
    if student_rows.empty:
        flash(f'Student with ID {student_id} not found.', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    student = student_rows.iloc[0].to_dict()
    original_index = student_rows.index[0]
    
    # Get all available semesters from columns
    semesters = []
    for col in df.columns:
        if ' - ' in col:
            parts = col.rsplit(' - ', 1)
            if len(parts) == 2:
                semester_part = parts[1].strip()
                # Only add standard semesters (exclude malformed ones like "Semester 1.1")
                if '.' not in semester_part and 'Semester' in semester_part:
                    semesters.append(semester_part)
    
    semesters = sorted(list(set(semesters)), key=lambda x: x.split()[-1] if x else '')
    
    if request.method == 'POST':
        selected_semester = request.form.get('semester', '')
        
        if not selected_semester:
            flash('Please select a semester to edit.', 'warning')
            return redirect(url_for('admin_edit_student_results', student_id=student_id))
        
        try:
            # Update all score columns for this semester
            fields_updated = 0
            for col in df.columns:
                if selected_semester in str(col):
                    field_name = str(col).replace(f' - {selected_semester}', '')
                    form_key = f"score_{field_name}"
                    value = request.form.get(form_key, '')
                    
                    if value != '':
                        df.loc[original_index, col] = value
                        fields_updated += 1
            
            print(f"DEBUG: Updated {fields_updated} fields in DataFrame for student {student_id}")
            
            # Save back to Google Sheet using the new fix function
            success = save_results_to_sheet_fix(df)
            
            if success:
                flash(f'Results saved! {fields_updated} fields updated for {selected_semester}.', 'success')
            else:
                flash('Error: Could not save to Google Sheet. Check console for details.', 'danger')
            
            return redirect(url_for('admin_edit_student_results', student_id=student_id))
            
        except Exception as e:
            flash(f'Error: {str(e)}', 'danger')
            print(f"ERROR in edit results POST: {e}")
            import traceback
            traceback.print_exc()
    
    return render_template('admin_student_results_edit.html',
                           student=student,
                           semesters=semesters,
                           all_columns=df.columns.tolist())
# ====== END OF UPDATED FUNCTION ======

@app.route('/api/student/<student_id>/results', methods=['GET'])
def api_get_student_results(student_id):
    """API endpoint to get student results as JSON."""
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Not authorized'}), 401
    
    try:
        df = load_results_from_sheet()
        
        if df.empty:
            return jsonify({'success': False, 'message': 'No data found'}), 404
        
        student_rows = df[df['Student ID'].astype(str) == str(student_id)]
        
        if student_rows.empty:
            return jsonify({'success': False, 'message': 'Student not found'}), 404
        
        student_data = student_rows.iloc[0].to_dict()
        
        return jsonify({'success': True, 'data': student_data})
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/student/<student_id>/results', methods=['POST'])
def api_save_student_results(student_id):
    """API endpoint to save student results from JSON payload."""
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Not authorized'}), 401
    
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400
        
        df = load_results_from_sheet()
        
        if df.empty:
            return jsonify({'success': False, 'message': 'Unable to load data'}), 500
        
        student_rows = df[df['Student ID'].astype(str) == str(student_id)]
        
        if student_rows.empty:
            return jsonify({'success': False, 'message': 'Student not found'}), 404
        
        idx = student_rows.index[0]
        
        # Update student info fields
        for field in ['Student Name', 'Student Department', 'Parent Phone']:
            if field in data:
                df.loc[idx, field] = data[field]
        
        # Update score columns
        for col in df.columns:
            if col in data and col not in ['Student ID', 'Student Name', 'Student Department', 'Parent Phone']:
                df.loc[idx, col] = data[col]
        
        # Save back to Google Sheet
        save_results_to_sheet(df)
        
        return jsonify({'success': True, 'message': 'Results saved successfully'})
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# HOD Routes for Student Results Management
@app.route('/hod/students')
def hod_students():
    """HOD view of students in their department."""
    if not session.get('hod_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('hod_login'))
    
    department = session.get('hod_department')
    search_query = request.args.get('search', '')
    
    # Load all students
    df = load_results_from_sheet()
    
    if df.empty:
        students = []
    else:
        # Filter by department
        students_df = df[df['Student Department'].astype(str).str.lower() == department.lower()]
        
        # Apply search filter
        if search_query:
            students_df = students_df[
                students_df['Student Name'].astype(str).str.contains(search_query, case=False, na=False) |
                students_df['Student ID'].astype(str).str.contains(search_query, case=False, na=False)
            ]
        
        students = students_df.to_dict('records')
    
    return render_template('hod_students.html',
                           students=students,
                           department=department,
                           search_query=search_query)


@app.route('/hod/student/<student_id>/view')
def hod_view_student(student_id):
    """HOD view a student's full results."""
    if not session.get('hod_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('hod_login'))
    
    student_full_data = get_all_student_results_by_id(student_id)
    
    if not student_full_data['student_info']:
        flash(f"Student with ID {student_id} not found.", 'danger')
        return redirect(url_for('hod_students'))
    
    return render_template('hod_student_view.html',
                           student_data=student_full_data,
                           department=session.get('hod_department'))


@app.route('/hod/student/<student_id>/results', methods=['GET', 'POST'])
def hod_edit_student_results(student_id):
    """HOD can add remarks to student results."""
    if not session.get('hod_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('hod_login'))
    
    student_full_data = get_all_student_results_by_id(student_id)
    
    if not student_full_data['student_info']:
        flash(f"Student with ID {student_id} not found.", 'danger')
        return redirect(url_for('hod_students'))
    
    student = student_full_data['student_info']
    
    # Get all available semesters
    semesters = list(student_full_data['results_by_semester'].keys())
    
    if request.method == 'POST':
        semester = request.form.get('semester', '')
        subject = request.form.get('subject', '')
        remarks = request.form.get('remarks', '').strip()
        
        if semester and subject and remarks:
            try:
                # Find the column for this subject's remarks in this semester
                remarks_col = None
                for col in get_all_columns():
                    if subject in col and semester in col and 'Remarks' in col:
                        remarks_col = col
                        break
                
                if remarks_col:
                    df = load_results_from_sheet()
                    idx = df[df['Student ID'].astype(str) == str(student_id)].index[0]
                    df.loc[idx, remarks_col] = remarks
                    save_results_to_sheet(df)
                    
                    flash(f'Remarks for {subject} ({semester}) added successfully!', 'success')
                else:
                    flash('Could not find the remarks column for this subject.', 'warning')
                    
            except Exception as e:
                flash(f'Error saving remarks: {str(e)}', 'danger')
        else:
            flash('Please fill in all fields.', 'warning')
    
    return render_template('hod_student_results.html',
                           student=student,
                           semesters=semesters,
                           results_by_semester=student_full_data['results_by_semester'],
                           department=session.get('hod_department'))


def get_all_columns():
    """Helper function to get all columns from the sheet."""
    try:
        df = load_results_from_sheet()
        return df.columns.tolist()
    except:
        return []


def save_results_to_sheet(df):
    """
    Save the DataFrame back to Google Sheet using UNIFIED_GOOGLE_SHEET_ID.
    This replaces all data in the Students worksheet.
    """
    try:
        gc = get_google_sheet_client()
        if not gc:
            print("Failed to get Google Sheets client")
            return False
        
        sh = gc.open_by_key(UNIFIED_GOOGLE_SHEET_ID)
        
        # Try to get the 'Students' worksheet, fallback to sheet1
        try:
            worksheet = sh.worksheet('Students')
        except:
            worksheet = sh.sheet1
        
        # Clear existing data
        worksheet.clear()
        
        # Prepare all values - headers then data
        all_values = [df.columns.tolist()] + df.values.tolist()
        
        # Convert any non-string values to strings
        clean_values = []
        for row in all_values:
            clean_row = []
            for val in row:
                if pd.isna(val):
                    clean_row.append('')
                elif isinstance(val, (int, float)):
                    clean_row.append(str(val))
                else:
                    clean_row.append(str(val))
            clean_values.append(clean_row)
        
        # Calculate range dynamically with proper column letter handling
        num_rows = len(clean_values)
        num_cols = len(df.columns)
        
        def col_to_letter(n):
            """Convert column number (1-based) to Excel letter format"""
            result = ""
            while n > 0:
                n, remainder = divmod(n - 1, 26)
                result = chr(65 + remainder) + result
            return result
        
        end_col = col_to_letter(num_cols)
        range_name = f"A1:{end_col}{num_rows}"
        
        # Update with new data
        worksheet.update(values=clean_values, range_name=range_name)
        
        print(f"Successfully saved {len(df)} rows to Google Sheet Students worksheet")
        return True
        
    except Exception as e:
        print(f"Error saving to Google Sheet: {e}")
        import traceback
        traceback.print_exc()
        return False


# NEW ROUTE: Send all results via SMS for a single student
@app.route('/admin/student/<student_id>/send_all_sms')
def send_student_all_results_sms(student_id):
    if not session.get('admin_logged_in'):
        flash('Please log in to perform this action.', 'warning')
        return redirect(url_for('admin_login'))

    student_full_data = get_all_student_results_by_id(student_id)
    student_info = student_full_data['student_info']
    results_by_semester = student_full_data['results_by_semester']

    if not student_info or not results_by_semester:
        flash(f"No results found for student ID {student_id} to send via SMS.", 'warning')
        return redirect(url_for('admin_dashboard'))

    phone_number = student_info.get('Parent Phone', '')
    student_name = student_info.get('Student Name', 'N/A')
    student_department = student_info.get('Student Department', 'N/A')

    if not phone_number or phone_number == 'N/A':
        flash(f"No valid phone number found for {student_name} ({student_id}). Cannot send SMS.", 'danger')
        return redirect(url_for('admin_dashboard'))

    all_sms_messages = []
    
    # Construct SMS content for each semester
    for semester_key in sorted(results_by_semester.keys()):
        semester_data = results_by_semester[semester_key]
        message_lines = [f"Dear Parent of {student_name},"]
        if student_department != 'N/A':
            message_lines.append(f"Dept: {student_department}")
        message_lines.append(f"Results for {semester_key}:")

        # Combine subject details for SMS
        all_subjects_in_semester = []
        if semester_data.get('Core Subjects'):
            all_subjects_in_semester.extend(semester_data['Core Subjects'])
        if semester_data.get('Elective Subjects'):
            all_subjects_in_semester.extend(semester_data['Elective Subjects'])

        for subject_data in all_subjects_in_semester:
            subject = subject_data.get('Subject', 'N/A')
            exams_score = subject_data.get('Exams Score', 'N/A')
            class_score = subject_data.get('Class Score', 'N/A')
            total_score = subject_data.get('Total Score', 'N/A')
            grade = subject_data.get('Grade', 'N/A')
            remarks = subject_data.get('Remarks', 'N/A')
            
            message_lines.append(f" {subject}: Ex={exams_score}, Cl={class_score}, Tot={total_score}, Grd={grade}, Rmk={remarks}")
        
        all_sms_messages.append("\n".join(message_lines))

    # Add login details to the LAST SMS message (or as a separate one)
    login_info_message = f"\nYour login credentials to view results online:\nWebsite: {WEBSITE_DOMAIN}{url_for('student_login')}\nStudent Name: {student_name}\nParent Phone: {phone_number}"
    all_sms_messages.append(login_info_message) # Append to the list of messages

    success_count = 0
    failure_count = 0
    for i, sms_content in enumerate(all_sms_messages):
        print(f"Sending SMS part {i+1} to {phone_number}: {sms_content}") # Debug print
        send_success, send_message = send_sms(phone_number, sms_content)
        if send_success:
            success_count += 1
        else:
            failure_count += 1
            flash(f"Failed to send SMS part {i+1} to {student_name}: {send_message}", 'danger')

    if success_count > 0:
        flash(f"Successfully sent {success_count} SMS parts to {student_name}'s parent.", 'success')
    if failure_count > 0:
        flash(f"Failed to send {failure_count} SMS parts to {student_name}'s parent. Check logs for details.", 'danger')

    return redirect(url_for('admin_dashboard'))


# This route sends ALL results to ALL parents (use with caution!)
@app.route('/admin/send_all_sms_to_all_parents')
def admin_send_all_sms_to_all_parents():
    if not session.get('admin_logged_in'):
        flash('Please log in to perform this action.', 'warning')
        return redirect(url_for('admin_login'))

    df = load_results_from_sheet()
    if df.empty:
        flash("Error loading data to send SMS to all parents.", 'danger')
        return redirect(url_for('admin_dashboard'))

    overall_sent_count = 0
    overall_failed_count = 0
    
    for index, student_row_data in df.iterrows():
        student_id = student_row_data.get(COLUMN_MAPPING.get('Student ID'), 'N/A')
        student_name = student_row_data.get(COLUMN_MAPPING.get('Student Name'), 'N/A')
        phone_number = student_row_data.get(COLUMN_MAPPING.get('Parent Phone'), 'N/A')
        student_department = student_row_data.get(COLUMN_MAPPING.get('Student Department'), 'N/A')

        if not phone_number or phone_number == 'N/A':
            flash(f"Skipping SMS for {student_name} ({student_id}): No valid phone number found.", 'warning')
            overall_failed_count += 1
            continue

        student_full_data = get_all_student_results_by_id(student_id)
        results_by_semester = student_full_data['results_by_semester']

        if not results_by_semester:
            flash(f"No results found for {student_name} ({student_id}) to send via SMS.", 'info')
            overall_failed_count += 1
            continue

        all_sms_messages_for_student = []
        
        for semester_key in sorted(results_by_semester.keys()):
            semester_data = results_by_semester[semester_key]
            message_lines = [f"Dear Parent of {student_name},"]
            if student_department != 'N/A':
                message_lines.append(f"Dept: {student_department}")
            message_lines.append(f"Results for {semester_key}:")

            # Combine subject details for SMS
            all_subjects_in_semester = []
            if semester_data.get('Core Subjects'):
                all_subjects_in_semester.extend(semester_data['Core Subjects'])
            if semester_data.get('Elective Subjects'):
                all_subjects_in_semester.extend(semester_data['Elective Subjects'])

            for subject_data in all_subjects_in_semester:
                subject = subject_data.get('Subject', 'N/A')
                exams_score = subject_data.get('Exams Score', 'N/A')
                class_score = subject_data.get('Class Score', 'N/A')
                total_score = subject_data.get('Total Score', 'N/A')
                grade = subject_data.get('Grade', 'N/A')
                remarks = subject_data.get('Remarks', 'N/A')
                
                # Format each subject's details concisely
                message_lines.append(f" {subject}: Ex={exams_score}, Cl={class_score}, Tot={total_score}, Grd={grade}, Rmk={remarks}")
            
            all_sms_messages_for_student.append("\n".join(message_lines))

        login_info_message = f"\nYour login credentials to view results online:\nWebsite: {WEBSITE_DOMAIN}{url_for('student_login')}\nStudent Name: {student_name}\nParent Phone: {phone_number}"
        all_sms_messages_for_student.append(login_info_message)

        sent_parts_for_student = 0
        failed_parts_for_student = 0
        for sms_content in all_sms_messages_for_student:
            send_success, send_message = send_sms(phone_number, sms_content)
            if send_success:
                sent_parts_for_student += 1
            else:
                failed_parts_for_student += 1
                print(f"Failed to send SMS part to {student_name}: {send_message}") # Log detailed failure

        if sent_parts_for_student > 0:
            overall_sent_count += 1
            flash(f"SMS sent to {student_name}'s parent ({sent_parts_for_student} parts sent).", 'success')
        if failed_parts_for_student > 0:
            overall_failed_count += 1
            flash(f"SMS failed for {student_name}'s parent ({failed_parts_for_student} parts failed).", 'danger')

    flash(f"Batch SMS sending complete. Sent to {overall_sent_count} students, failed for {overall_failed_count} students.", 'info')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/send_pta_message_form', methods=['GET'])
def send_pta_message_form():
    """Displays the form for sending a custom PTA message."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    return render_template('send_pta_message_form.html')

@app.route('/admin/send_pta_message', methods=['POST'])
def send_pta_message():
    """Handles sending a custom PTA message to all parents."""
    if not session.get('admin_logged_in'):
        flash('Please log in to perform this action.', 'warning')
        return redirect(url_for('admin_login'))

    message_content = request.form.get('message_content')
    if not message_content:
        flash('Message content cannot be empty.', 'danger')
        return redirect(url_for('send_pta_message_form'))

    df = load_results_from_sheet()
    if df.empty:
        flash("Error loading student data to send PTA message.", 'danger')
        return redirect(url_for('admin_dashboard'))

    # Ensure the 'Parent Phone' and 'Student Name' columns exist
    if COLUMN_MAPPING.get('Parent Phone') not in df.columns or COLUMN_MAPPING.get('Student Name') not in df.columns:
        flash(f"Error: Required columns ('{COLUMN_MAPPING.get('Parent Phone')}' or '{COLUMN_MAPPING.get('Student Name')}') not found in the Google Sheet. Cannot send PTA message.", 'danger')
        return redirect(url_for('admin_dashboard'))

    sent_count = 0
    failed_count = 0

    # Iterate through each student record to personalize and send messages
    for index, row in df.iterrows():
        student_name = row.get(COLUMN_MAPPING['Student Name'], 'N/A')
        parent_phone = row.get(COLUMN_MAPPING['Parent Phone'], 'N/A')

        # Skip if student name or phone is missing/invalid for this row
        if student_name == 'N/A' or not parent_phone or parent_phone.strip() == 'N/A' or parent_phone.strip() == '':
            print(f"Skipping PTA message for row {index}: Missing student name or valid parent phone.")
            failed_count += 1 # Count as a failure for this specific student's message
            continue

        # Construct the personalized message
        personalized_message = f"Dear Parent of {student_name}, {message_content}"

        send_success, send_message_status = send_sms(parent_phone, personalized_message)
        if send_success:
            sent_count += 1
            print(f"Successfully sent PTA message to {student_name}'s parent ({parent_phone}).")
        else:
            failed_count += 1
            print(f"Failed to send PTA message to {student_name}'s parent ({parent_phone}): {send_message_status}")

    if sent_count > 0:
        flash(f"Successfully sent PTA message to {sent_count} student parents.", 'success')
    if failed_count > 0:
        flash(f"Failed to send PTA message to {failed_count} student parents. Check logs for details.", 'danger')
    elif sent_count == 0 and failed_count == 0: # No valid students found in the sheet
        flash("No valid student records with phone numbers found in the database to send the PTA message.", 'warning')

    return redirect(url_for('admin_dashboard'))


# --- New Route: Department Share Links Management ---
@app.route('/admin/manage_share_links', methods=['GET'])
def manage_share_links():
    """Displays the admin page for managing department share links."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Filter out expired links but keep track of them
    active_links = {token: data for token, data in DEPARTMENT_SHARE_LINKS.items() 
                   if not data['used'] and datetime.now() < data['expires_at']}
    
    # Get expired links for display (optional - could remove expired ones)
    expired_links = {token: data for token, data in DEPARTMENT_SHARE_LINKS.items() 
                    if data['used'] or datetime.now() >= data['expires_at']}
    
    return render_template('manage_share_links.html', 
                           active_links=active_links,
                           expired_links=expired_links,
                           available_departments=AVAILABLE_DEPARTMENTS,
                           available_years=AVAILABLE_YEARS,
                           available_semesters=AVAILABLE_GENERIC_SEMESTER_TYPES)


@app.route('/admin/generate_share_link', methods=['POST'])
def generate_share_link():
    """Generates a new shareable link for a department head to upload results."""
    if not session.get('admin_logged_in'):
        flash('Please log in to perform this action.', 'warning')
        return redirect(url_for('admin_login'))
    
    department = request.form.get('department')
    year = request.form.get('year')
    semester = request.form.get('semester')
    expires_days = request.form.get('expires_days', '7')
    
    if not department or not year or not semester:
        flash('Please select department, year, and semester.', 'warning')
        return redirect(url_for('manage_share_links'))
    
    try:
        expires_days = int(expires_days)
        if expires_days < 1 or expires_days > 30:
            expires_days = 7
    except ValueError:
        expires_days = 7
    
    # Create the share link
    share_url, token, expires_at = create_share_link(department, year, semester, expires_days)
    
    flash(f'Share link generated successfully for {department} ({year} - {semester}). Link will expire on {expires_at.strftime("%Y-%m-%d %H:%M")}.', 'success')
    
    # Return the generated link information to be displayed
    return render_template('manage_share_links.html',
                           share_link_generated=True,
                           share_url=share_url,
                           expires_at=expires_at,
                           existing_links={token: DEPARTMENT_SHARE_LINKS[token] for token in DEPARTMENT_SHARE_LINKS 
                                       if not DEPARTMENT_SHARE_LINKS[token]['used'] and datetime.now() < DEPARTMENT_SHARE_LINKS[token]['expires_at']},
                           available_departments=AVAILABLE_DEPARTMENTS,
                           available_years=AVAILABLE_YEARS,
                           available_semesters=AVAILABLE_GENERIC_SEMESTER_TYPES)


@app.route('/department_upload/<token>', methods=['GET', 'POST'])
def department_upload(token):
    """Allows department heads to upload results using a secure share link."""
    # Validate the token
    link_data, error = validate_share_token(token)
    
    if error:
        flash(error, 'danger')
        return render_template('department_upload.html', valid_token=False, error=error, token=token)
    
    department = link_data['department']
    year = link_data['year']
    semester = link_data['semester']
    expires_at = link_data['expires_at']
    
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file part in the request.', 'warning')
            return redirect(request.url)
        
        file = request.files['file']
        
        if file.filename == '':
            flash('No selected file.', 'warning')
            return redirect(request.url)
        
        if file and (file.filename.endswith('.xlsx') or file.filename.endswith('.xls')):
            try:
                # Read the Excel file
                df = pd.read_excel(file)
                
                # Clean column names
                df.columns = df.columns.str.strip()
                
                # Validate that required columns exist
                required_cols = ['Student ID']
                missing_cols = [col for col in required_cols if col not in df.columns]
                
                if missing_cols:
                    flash(f'Missing required columns in Excel file: {missing_cols}. The file must contain at least Student ID.', 'danger')
                    return redirect(request.url)
                
                # Generate semester key
                semester_key = f"{year} - {semester}"
                
                # Get the subjects for this department and semester
                subjects_in_semester = FULL_SUBJECT_DETAILS_BY_SEMESTER_KEY.get(semester_key, {})
                student_electives = ELECTIVE_SUBJECT_NAMES_BY_DEPARTMENT.get(department, [])
                
                # Build list of columns to update
                columns_to_update = []
                
                # Add core subject columns
                for subject_name, details in subjects_in_semester.get('Core Subjects', {}).items():
                    for score_type, col_name in details.items():
                        if col_name:
                            columns_to_update.append(col_name)
                
                # Add elective subject columns (only for this department)
                for subject_name, details in subjects_in_semester.get('Elective Subjects', {}).items():
                    if subject_name in student_electives:
                        for score_type, col_name in details.items():
                            if col_name:
                                columns_to_update.append(col_name)
                
                # Process the uploaded data
                processed_count = 0
                skipped_count = 0
                error_messages = []
                
                # Load existing data to find matching students
                existing_df = load_results_from_sheet()
                student_id_col = COLUMN_MAPPING.get('Student ID')
                
                if student_id_col and student_id_col in existing_df.columns:
                    existing_df[student_id_col] = existing_df[student_id_col].astype(str).str.strip()
                    
                    # Get Google Sheets client for writing
                    gc = get_google_sheet_client()
                    if gc:
                        try:
                            sh = gc.open_by_key(UNIFIED_GOOGLE_SHEET_ID)
                            worksheet = sh.sheet1  # Assuming data is in the first sheet
                            all_values = worksheet.get_all_records()
                            
                            # Process each row in the uploaded file
                            for index, row in df.iterrows():
                                student_id = str(row.get('Student ID', '')).strip()
                                
                                if not student_id:
                                    skipped_count += 1
                                    continue
                                
                                # Find matching student in the sheet
                                for idx, existing_row in existing_df.iterrows():
                                    if str(existing_row.get(student_id_col, '')).strip() == student_id:
                                        # Verify department matches
                                        existing_department = existing_row.get(COLUMN_MAPPING.get('Student Department'), '')
                                        if existing_department != department:
                                            skipped_count += 1
                                            break
                                        
                                        # Update the row in Google Sheets
                                        try:
                                            # Find the actual row number (2-indexed for header)
                                            actual_row_num = idx + 2
                                            
                                            # Build update dictionary for this student's columns
                                            update_data = {}
                                            for col in columns_to_update:
                                                if col in df.columns:
                                                    value = row.get(col, '')
                                                    # Handle NaN values
                                                    if pd.isna(value):
                                                        value = ''
                                                    update_data[col] = value
                                            
                                            if update_data:
                                                # Update each cell in the row
                                                for col_name, value in update_data.items():
                                                    # Find column index
                                                    if all_values and col_name in all_values[0]:
                                                        col_idx = list(all_values[0]).index(col_name) + 1
                                                        worksheet.update_cell(actual_row_num, col_idx, str(value))
                                                
                                                processed_count += 1
                                            else:
                                                skipped_count += 1
                                            
                                        except Exception as e:
                                            error_messages.append(f"Error updating row for student {student_id}: {e}")
                                            skipped_count += 1
                                        
                                        break
                                else:
                                    skipped_count += 1
                            
                            # Refresh metadata to include any new columns
                            initialize_sheet_metadata()
                            
                            if processed_count > 0:
                                flash(f'Successfully updated {processed_count} student records for {department} ({semester_key}).', 'success')
                            if skipped_count > 0:
                                flash(f'Skipped {skipped_count} records. Please check that students exist and are in the correct department.', 'warning')
                            if error_messages:
                                for error_msg in error_messages[:5]:  # Show first 5 errors
                                    flash(error_msg, 'danger')
                            
                            # Mark the token as used after successful upload
                            mark_token_used(token)
                            
                        except Exception as e:
                            flash(f'Error accessing Google Sheets: {e}. Please try again later.', 'danger')
                            print(f"Google Sheets update error: {e}")
                    else:
                        flash('Could not connect to Google Sheets. Please contact the administrator.', 'danger')
                else:
                    flash('Student ID column not found in the database.', 'danger')
                
                return redirect(url_for('department_upload', token=token))
                
            except Exception as e:
                flash(f'Error processing Excel file: {e}', 'danger')
                print(f"Department upload error: {e}")
                return redirect(request.url)
        else:
            flash('Invalid file type. Please upload an Excel file (.xlsx or .xls).', 'warning')
            return redirect(request.url)
    
    # GET request - show the upload form
    return render_template('department_upload.html',
                           valid_token=True,
                           token=token,
                           department=department,
                           year=year,
                           semester=semester,
                           expires_at=expires_at)


@app.route('/admin/revoke_share_link/<token>')
def revoke_share_link(token):
    """Revokes a share link by marking it as used."""
    if not session.get('admin_logged_in'):
        flash('Please log in to perform this action.', 'warning')
        return redirect(url_for('admin_login'))
    
    if token in DEPARTMENT_SHARE_LINKS:
        mark_token_used(token)
        flash('Share link has been revoked successfully.', 'success')
    else:
        flash('Share link not found.', 'warning')
    
    return redirect(url_for('manage_share_links'))


# --- Route: Download Excel Template ---
@app.route('/admin/download_template')
def download_template():
    """Generates and downloads an Excel template for uploading results."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Get selected year and semester from query parameters
    selected_year = request.args.get('year', str(datetime.now().year))
    selected_semester = request.args.get('semester', 'Semester 1')
    selected_department = request.args.get('department', 'all')
    
    semester_key = f"{selected_year} - {selected_semester}"
    
    # Build template DataFrame
    # Fixed columns (Student ID, Student Name, Student Department are required)
    template_data = {
        'Student ID': [],
        'Student Name': [],
        'Student Department': [],
        'Parent Phone': []  # Optional - can be left blank
    }
    
    # Add subject columns based on selected department or all departments
    subjects_to_include = set(CORE_SUBJECT_NAMES)
    
    if selected_department == 'all':
        # Include all elective subjects from all departments
        for dept_electives in ELECTIVE_SUBJECT_NAMES_BY_DEPARTMENT.values():
            subjects_to_include.update(dept_electives)
    elif selected_department in ELECTIVE_SUBJECT_NAMES_BY_DEPARTMENT:
        # Include only core subjects + this department's electives
        subjects_to_include.update(ELECTIVE_SUBJECT_NAMES_BY_DEPARTMENT[selected_department])
    
    # Add columns for each subject
    score_types = ['Exams Score', 'Class Score', 'Total Score', 'Grade', 'Remarks']
    for subject in sorted(subjects_to_include):
        for score_type in score_types:
            template_data[f"{subject} {score_type} - {semester_key}"] = []
    
    # Create template DataFrame with one empty row
    template_df = pd.DataFrame(template_data)
    
    # Add a few example rows
    example_students = [
        {'Student ID': 'EET001', 'Student Name': 'ABAYOM JOSEPH', 'Student Department': 'electricals', 'Parent Phone': '0244111111'},
        {'Student ID': 'EET002', 'Student Name': 'ABE Y ELE DAVID', 'Student Department': 'electricals', 'Parent Phone': '0244222222'},
        {'Student ID': 'EET003', 'Student Name': 'ABOAGYEWAA BLESSING', 'Student Department': 'electricals', 'Parent Phone': '0244333333'},
    ]
    
    for example in example_students:
        template_df = pd.concat([template_df, pd.DataFrame([example])], ignore_index=True)
    
    # Create Excel file in memory
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        template_df.to_excel(writer, index=False, sheet_name=f'{selected_semester} Results')
    
    output.seek(0)
    
    # Generate filename
    filename = f"results_template_{selected_year}_{selected_semester.replace(' ', '_')}.xlsx"
    
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


# --- Helper function: Load Results Flexibly ---
def load_results_flexible():
    """
    Load results from Google Sheet if available, otherwise fall back to local Excel.
    Returns a DataFrame (empty DataFrame if both fail).
    """
    # First try Google Sheets
    df = load_results_from_sheet()
    if not df.empty:
        return df, 'google'
    
    # Fall back to local Excel
    try:
        excel_path = get_excel_path('students')
        if os.path.exists(excel_path):
            df = pd.read_excel(excel_path)
            if not df.empty:
                return df, 'excel'
    except Exception as e:
        print(f"Error loading from local Excel: {e}")
    
    return pd.DataFrame(), 'none'


@app.route('/admin/upload_excel_results', methods=['GET', 'POST'])
def upload_excel_results():
    """Allows admin to upload Excel files with student results using wide format (all subjects in one row per student).
    Supports offline mode by falling back to local Excel when Google Sheets is unavailable."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file part in the request.', 'warning')
            return redirect(request.url)
        
        file = request.files['file']
        
        if file.filename == '':
            flash('No selected file.', 'warning')
            return redirect(request.url)
        
        if file and (file.filename.endswith('.xlsx') or file.filename.endswith('.xls')):
            try:
                # Read the Excel file
                df = pd.read_excel(file)
                
                # Clean column names (remove leading/trailing spaces)
                df.columns = df.columns.str.strip()
                
                # Validate that required columns exist (Student ID, Name, Department are required - Parent Phone is optional)
                required_cols = ['Student ID', 'Student Name', 'Student Department']
                missing_cols = [col for col in required_cols if col not in df.columns]
                
                if missing_cols:
                    flash(f'Missing required columns in Excel file: {", ".join(missing_cols)}. Please ensure your Excel file has Student ID, Student Name, and Student Department columns.', 'danger')
                    return redirect(request.url)
                
                # Load existing data - try Google Sheets first, fall back to local Excel
                existing_df, data_source = load_results_flexible()
                
                if existing_df.empty:
                    flash('Could not load existing student data. No data found in Google Sheets or local Excel.', 'danger')
                    return redirect(request.url)
                
                # Determine the student ID column
                student_id_col = COLUMN_MAPPING.get('Student ID')
                if not student_id_col or student_id_col not in existing_df.columns:
                    # Try to find any column that might be Student ID
                    possible_cols = ['Student ID', 'student_id', 'ID', 'id']
                    for col in possible_cols:
                        if col in existing_df.columns:
                            student_id_col = col
                            break
                
                if not student_id_col or student_id_col not in existing_df.columns:
                    flash('Student ID column not found in the main database.', 'danger')
                    return redirect(request.url)
                
                # Prepare existing data for matching
                existing_df[student_id_col] = existing_df[student_id_col].astype(str).str.strip()
                
                # Try to get Google Sheets client for writing
                gc = get_google_sheet_client()
                google_available = False
                worksheet = None
                sheet_headers = []
                
                if gc:
                    try:
                        sh = gc.open_by_key(GOOGLE_SHEET_ID)
                        worksheet = sh.sheet1
                        all_records = worksheet.get_all_records()
                        
                        if all_records:
                            sheet_headers = list(all_records[0].keys())
                        
                        google_available = True
                        print("Google Sheets connection successful")
                    except Exception as e:
                        print(f"Google Sheets not available: {e}")
                        flash('Google Sheets not accessible. Data will be saved to local Excel only.', 'warning')
                else:
                    flash('Google Sheets not accessible. Data will be saved to local Excel only.', 'warning')
                
                # Auto-detect semester from column headers
                detected_semesters = set()
                import re
                
                for col in df.columns:
                    # Pattern: "Subject Score Type - YYYY Semester X" or similar
                    # Look for year (4 digits) and semester indicator
                    year_match = re.search(r'\b(20\d{2})\b', col)
                    semester_match = re.search(r'(Semester|Sem)\s*[12]', col, re.IGNORECASE)
                    
                    if year_match and semester_match:
                        year = year_match.group(1)
                        sem_type = semester_match.group(0)
                        # Normalize semester type
                        if '1' in sem_type:
                            sem_type = 'Semester 1'
                        else:
                            sem_type = 'Semester 2'
                        detected_semesters.add(f"{year} - {sem_type}")
                
                if not detected_semesters:
                    flash('Could not detect semester from column headers. Please ensure headers follow the format: "Subject Score Type - YYYY Semester X"', 'danger')
                    return redirect(request.url)
                
                # Process each row in the uploaded file
                updated_count = 0
                skipped_count = 0
                error_messages = []
                
                for index, row in df.iterrows():
                    try:
                        student_id = str(row.get('Student ID', '')).strip()
                        
                        if not student_id:
                            skipped_count += 1
                            continue
                        
                        # Find the student in existing data
                        matching_rows = existing_df[existing_df[student_id_col] == student_id]
                        
                        if matching_rows.empty:
                            skipped_count += 1
                            continue
                        
                        # Get the row index in the existing data
                        row_idx = matching_rows.index[0]
                        
                        # Update existing DataFrame with new scores
                        for col_name, value in row.items():
                            # Skip non-score columns
                            if col_name in ['Student ID', 'Student Name', 'Student Department', 'Parent Phone']:
                                continue
                            
                            # Check if this column is a score column (contains year and semester)
                            is_score_column = False
                            for semester_key in detected_semesters:
                                # Check if the column header follows the expected pattern
                                if f" - {semester_key}" in col_name:
                                    is_score_column = True
                                    break
                            
                            if not is_score_column:
                                continue
                            
                            # Handle NaN and None values
                            if pd.isna(value):
                                value = ''
                            else:
                                value = str(value).strip()
                            
                            # Check if column exists in existing_df, if not add it
                            if col_name not in existing_df.columns:
                                existing_df[col_name] = ''
                            
                            # Update the value
                            existing_df.loc[row_idx, col_name] = value
                            
                            # Update Google Sheets cell if available
                            if google_available and worksheet and col_name in sheet_headers:
                                try:
                                    sheet_row_idx = row_idx + 2  # 1-indexed + header row
                                    col_idx = sheet_headers.index(col_name) + 1
                                    worksheet.update_cell(sheet_row_idx, col_idx, value)
                                except Exception as e:
                                    print(f"Error updating cell for student {student_id}, column {col_name}: {e}")
                        
                        updated_count += 1
                        
                    except Exception as e:
                        error_messages.append(f"Row {index + 1}: {str(e)}")
                        skipped_count += 1
                
                # Save to local Excel file
                try:
                    excel_path = get_excel_path('students')
                    existing_df.to_excel(excel_path, index=False)
                    print(f"Saved {len(existing_df)} records to local Excel: {excel_path}")
                except Exception as e:
                    print(f"Error saving to local Excel: {e}")
                    error_messages.append(f"Error saving to local Excel: {str(e)}")
                
                # Also try to save back to Google Sheets
                if google_available:
                    save_results_to_sheet(existing_df)
                
                # Refresh metadata to include any new columns
                initialize_sheet_metadata()
                
                # Report results
                if updated_count > 0:
                    semesters_processed = ', '.join(sorted(detected_semesters))
                    if google_available:
                        flash(f'Successfully updated {updated_count} student records for {semesters_processed} (saved to Google Sheets and local Excel).', 'success')
                    else:
                        flash(f'Successfully updated {updated_count} student records for {semesters_processed} (saved to local Excel only).', 'success')
                
                if skipped_count > 0:
                    flash(f'Skipped {skipped_count} records (missing Student ID or student not found in database).', 'warning')
                
                if error_messages:
                    for error in error_messages[:3]:  # Show first 3 errors
                        flash(f'Error: {error}', 'danger')
                
                return redirect(url_for('admin_dashboard'))
                
            except Exception as e:
                flash(f'Error processing Excel file: {e}', 'danger')
                print(f"Excel processing error: {e}")
                import traceback
                traceback.print_exc()
                return redirect(request.url)
        else:
            flash('Invalid file type. Please upload an Excel file (.xlsx or .xls).', 'warning')
            return redirect(request.url)
    
    # GET request - show the upload form
    return render_template('upload_excel_results.html',
                           available_years=AVAILABLE_YEARS,
                           available_semesters=AVAILABLE_GENERIC_SEMESTER_TYPES,
                           available_departments=AVAILABLE_DEPARTMENTS)


# --- New Route: Send SMS by Semester Form ---
@app.route('/admin/send_semester_sms_form', methods=['GET'])
def send_semester_sms_form():
    """Displays the form for sending SMS for a specific semester."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    return render_template('send_semester_sms_form.html',
                           available_years=AVAILABLE_YEARS,
                           available_semesters=AVAILABLE_GENERIC_SEMESTER_TYPES)


# --- New Route: Process Send SMS by Semester ---
@app.route('/admin/send_semester_sms', methods=['POST'])
def send_semester_sms():
    """Sends SMS results for a selected semester to all parents."""
    if not session.get('admin_logged_in'):
        flash('Please log in to perform this action.', 'warning')
        return redirect(url_for('admin_login'))
    
    selected_year = request.form.get('year')
    selected_semester = request.form.get('semester')
    
    if not selected_year or not selected_semester:
        flash('Please select both Academic Year and Semester.', 'warning')
        return redirect(url_for('send_semester_sms_form'))
    
    semester_key = f"{selected_year} - {selected_semester}"
    
    # Check if this semester exists in the data
    if semester_key not in FULL_SUBJECT_DETAILS_BY_SEMESTER_KEY:
        flash(f'No results data found for {semester_key}. Please ensure results have been uploaded for this semester.', 'warning')
        return redirect(url_for('send_semester_sms_form'))
    
    df = load_results_from_sheet()
    if df.empty:
        flash("Error loading student data to send SMS.", 'danger')
        return redirect(url_for('admin_dashboard'))
    
    # Ensure required columns exist
    required_cols = [COLUMN_MAPPING.get('Student ID'), COLUMN_MAPPING.get('Student Name'), 
                     COLUMN_MAPPING.get('Parent Phone'), COLUMN_MAPPING.get('Student Department')]
    required_cols = [col for col in required_cols if col is not None]
    
    if not all(col in df.columns for col in required_cols):
        missing = [col for col in required_cols if col not in df.columns]
        flash(f'Missing required columns in sheet: {missing}. Cannot send SMS.', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    sent_count = 0
    failed_count = 0
    
    for index, student_row_data in df.iterrows():
        student_id = student_row_data.get(COLUMN_MAPPING.get('Student ID'), 'N/A')
        student_name = student_row_data.get(COLUMN_MAPPING.get('Student Name'), 'N/A')
        phone_number = student_row_data.get(COLUMN_MAPPING.get('Parent Phone'), 'N/A')
        student_department = student_row_data.get(COLUMN_MAPPING.get('Student Department'), 'N/A')
        
        if not phone_number or phone_number == 'N/A' or str(phone_number).strip() == '':
            print(f"Skipping SMS for {student_name} ({student_id}): No valid phone number.")
            failed_count += 1
            continue
        
        # Get student data
        student_full_data = get_all_student_results_by_id(student_id)
        results_by_semester = student_full_data['results_by_semester']
        
        # Check if the selected semester exists for this student
        if semester_key not in results_by_semester:
            print(f"Skipping SMS for {student_name}: No results for {semester_key}")
            continue
        
        semester_data = results_by_semester[semester_key]
        
        # Build the SMS message for this specific semester
        message_lines = [f"Dear Parent of {student_name},"]
        if student_department != 'N/A':
            message_lines.append(f"Dept: {student_department}")
        message_lines.append(f"Results for {semester_key}:")
        
        # Combine subject details for this semester
        all_subjects_in_semester = []
        if semester_data.get('Core Subjects'):
            all_subjects_in_semester.extend(semester_data['Core Subjects'])
        if semester_data.get('Elective Subjects'):
            all_subjects_in_semester.extend(semester_data['Elective Subjects'])
        
        if not all_subjects_in_semester:
            print(f"Skipping SMS for {student_name}: No subjects found for {semester_key}")
            continue
        
        for subject_data in all_subjects_in_semester:
            subject = subject_data.get('Subject', 'N/A')
            exams_score = subject_data.get('Exams Score', 'N/A')
            class_score = subject_data.get('Class Score', 'N/A')
            total_score = subject_data.get('Total Score', 'N/A')
            grade = subject_data.get('Grade', 'N/A')
            remarks = subject_data.get('Remarks', 'N/A')
            
            message_lines.append(f" {subject}: Ex={exams_score}, Cl={class_score}, Tot={total_score}, Grd={grade}, Rmk={remarks}")
        
        # Add login info
        login_info_message = f"\nView results online: {WEBSITE_DOMAIN}{url_for('student_login')}\nStudent: {student_name}, Phone: {phone_number}"
        message_lines.append(login_info_message)
        
        sms_content = "\n".join(message_lines)
        
        # Send the SMS
        send_success, send_message_status = send_sms(phone_number, sms_content)
        if send_success:
            sent_count += 1
            print(f"Successfully sent {semester_key} results to {student_name}'s parent ({phone_number}).")
        else:
            failed_count += 1
            print(f"Failed to send {semester_key} results to {student_name}'s parent ({phone_number}): {send_message_status}")
    
    if sent_count > 0:
        flash(f"Successfully sent {semester_key} results to {sent_count} student parents.", 'success')
    if failed_count > 0:
        flash(f"Failed to send results to {failed_count} parents. Check logs for details.", 'danger')
    if sent_count == 0 and failed_count == 0:
        flash(f"No valid students found with results for {semester_key}.", 'info')
    
    return redirect(url_for('admin_dashboard'))


# --- New Route: Send Single Student Semester SMS ---
@app.route('/admin/student/<student_id>/send_semester_sms')
def send_student_semester_sms(student_id):
    """Sends SMS results for a specific semester to a single student's parent."""
    if not session.get('admin_logged_in'):
        flash('Please log in to perform this action.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Get the semester from query parameters
    selected_year = request.args.get('year')
    selected_semester = request.args.get('semester')
    
    if not selected_year or not selected_semester:
        flash('Please select both Academic Year and Semester.', 'warning')
        return redirect(url_for('view_student_full_results', student_id=student_id))
    
    semester_key = f"{selected_year} - {selected_semester}"
    
    student_full_data = get_all_student_results_by_id(student_id)
    student_info = student_full_data['student_info']
    results_by_semester = student_full_data['results_by_semester']
    
    if not student_info:
        flash(f"Student with ID {student_id} not found.", 'danger')
        return redirect(url_for('admin_dashboard'))
    
    if semester_key not in results_by_semester:
        flash(f"No results found for {semester_key} for this student.", 'warning')
        return redirect(url_for('view_student_full_results', student_id=student_id))
    
    phone_number = student_info.get('Parent Phone', '')
    student_name = student_info.get('Student Name', 'N/A')
    student_department = student_info.get('Student Department', 'N/A')
    
    if not phone_number or phone_number == 'N/A':
        flash(f"No valid phone number found for {student_name}. Cannot send SMS.", 'danger')
        return redirect(url_for('view_student_full_results', student_id=student_id))
    
    semester_data = results_by_semester[semester_key]
    
    # Build the SMS message
    message_lines = [f"Dear Parent of {student_name},"]
    if student_department != 'N/A':
        message_lines.append(f"Dept: {student_department}")
    message_lines.append(f"Results for {semester_key}:")
    
    all_subjects_in_semester = []
    if semester_data.get('Core Subjects'):
        all_subjects_in_semester.extend(semester_data['Core Subjects'])
    if semester_data.get('Elective Subjects'):
        all_subjects_in_semester.extend(semester_data['Elective Subjects'])
    
    for subject_data in all_subjects_in_semester:
        subject = subject_data.get('Subject', 'N/A')
        exams_score = subject_data.get('Exams Score', 'N/A')
        class_score = subject_data.get('Class Score', 'N/A')
        total_score = subject_data.get('Total Score', 'N/A')
        grade = subject_data.get('Grade', 'N/A')
        remarks = subject_data.get('Remarks', 'N/A')
        
        message_lines.append(f" {subject}: Ex={exams_score}, Cl={class_score}, Tot={total_score}, Grd={grade}, Rmk={remarks}")
    
    login_info_message = f"\nView results online: {WEBSITE_DOMAIN}{url_for('student_login')}\nStudent: {student_name}, Phone: {phone_number}"
    message_lines.append(login_info_message)
    
    sms_content = "\n".join(message_lines)
    
    send_success, send_message_status = send_sms(phone_number, sms_content)
    
    if send_success:
        flash(f"Successfully sent {semester_key} results to {student_name}'s parent.", 'success')
    else:
        flash(f"Failed to send results to {student_name}'s parent: {send_message_status}", 'danger')
    
    return redirect(url_for('view_student_full_results', student_id=student_id))


@app.route('/student_result_pdf')
def student_result_pdf():
    """Generates a PDF report of a student's result using name and phone for verification."""
    
    # Check if WeasyPrint is available
    if not WEASYPRINT_AVAILABLE:
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <title>PDF Generation Unavailable</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; text-align: center; }
                .container { max-width: 600px; margin: 0 auto; }
                h1 { color: #dc3545; }
                .info { background-color: #f8f9fa; padding: 20px; border-radius: 8px; margin-top: 20px; }
                a { color: #007bff; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>PDF Generation Unavailable</h1>
                <div class="info">
                    <p><strong>Sorry, PDF generation is currently unavailable.</strong></p>
                    <p>This feature requires WeasyPrint and GTK3 libraries to be installed on the server.</p>
                    <p>Please contact the administrator to install the required dependencies.</p>
                    <p><a href="/">Return to Home</a></p>
                </div>
            </div>
        </body>
        </html>
        """, 503
    
    student_name = request.args.get('name')
    parent_phone = request.args.get('phone')

    print(f"PDF route received name: '{student_name}', phone: '{parent_phone}'") # Debug print

    if not student_name or not parent_phone:
        print("PDF generation: Missing student name or phone number in request args.") # Debug print
        return "Missing student name or phone number.", 400 # Bad Request

    df = load_results_from_sheet()
    if df.empty:
        print("PDF generation: Could not load results data from sheet.") # Debug print
        return "Could not load results data.", 500 # Internal Server Error

    # Find the student by matching name and phone number
    if COLUMN_MAPPING.get('Student Name') not in df.columns or COLUMN_MAPPING.get('Parent Phone') not in df.columns:
        print(f"PDF generation: Required columns for verification not found in sheet. Name Col: {COLUMN_MAPPING.get('Student Name')}, Phone Col: {COLUMN_MAPPING.get('Parent Phone')}") # Debug print
        return "Required columns for verification not found in sheet.", 500

    # Clean input name and phone for comparison
    cleaned_input_name = student_name.strip().lower()
    # Decode URL-encoded phone number before cleaning
    decoded_parent_phone = urllib.parse.unquote(parent_phone)
    cleaned_input_phone = decoded_parent_phone.strip().replace(" ", "").replace("-", "")


    df['_temp_name'] = df[COLUMN_MAPPING['Student Name']].astype(str).str.strip().str.lower()
    df['_temp_phone'] = df[COLUMN_MAPPING['Parent Phone']].astype(str).str.strip().replace(" ", "").replace("-", "").replace(".0", "", regex=False)

    print(f"PDF lookup: Comparing input name '{cleaned_input_name}' with sheet names (e.g., '{df['_temp_name'].iloc[0] if not df.empty else 'N/A'}')")
    print(f"PDF lookup: Comparing input phone '{cleaned_input_phone}' with sheet phones (e.g., '{df['_temp_phone'].iloc[0] if not df.empty else 'N/A'}')")

    student_row = df[
        (df['_temp_name'] == cleaned_input_name) &
        (df['_temp_phone'] == cleaned_input_phone)
    ]

    df = df.drop(columns=['_temp_name', '_temp_phone'])

    if student_row.empty:
        print(f"PDF generation: Student not found for name='{student_name}', phone='{parent_phone}' after lookup.") # Debug print
        return "Could not retrieve results. Please check the link or contact the school.", 404 # Not Found

    student_data_dict = student_row.iloc[0].to_dict()
    student_department = student_data_dict.get(COLUMN_MAPPING.get('Student Department'), 'N/A')

    # Prepare data for display in PDF template, including all subject details per semester
    display_results = {
        'Student Name': student_data_dict.get(COLUMN_MAPPING.get('Student Name'), 'N/A'),
        'Student ID': student_data_dict.get(COLUMN_MAPPING.get('Student ID'), 'N/A'),
        'Student Department': student_data_dict.get(COLUMN_MAPPING.get('Student Department'), 'N/A'),
        'Parent Phone': student_data_dict.get(COLUMN_MAPPING.get('Parent Phone'), 'N/A'), # ADDED: Parent Phone for PDF generation
        'Semesters': {}
    }

    student_electives_for_pdf = ELECTIVE_SUBJECT_NAMES_BY_DEPARTMENT.get(student_department, [])

    for semester_key in sorted(FULL_SUBJECT_DETAILS_BY_SEMESTER_KEY.keys()):
        subjects_in_semester_template = FULL_SUBJECT_DETAILS_BY_SEMESTER_KEY.get(semester_key, {})
        
        core_subjects_data_list = []
        elective_subjects_data_list = []
        has_data_for_semester = False

        for subject_name, details in subjects_in_semester_template.get('Core Subjects', {}).items():
            subject_info = {'Subject': subject_name}
            found_subject_data_for_this_subject = False
            for score_type_display, col_name in details.items():
                score_value = student_data_dict.get(col_name, 'N/A')
                subject_info[score_type_display] = score_value
                if score_value not in ['N/A', 'Column Missing', '', None]: # Check if value is meaningful
                    found_subject_data_for_this_subject = True
                    has_data_for_semester = True
            if found_subject_data_for_this_subject:
                core_subjects_data_list.append(subject_info)

        for subject_name, details in subjects_in_semester_template.get('Elective Subjects', {}).items():
            if subject_name in student_electives_for_pdf:
                subject_info = {'Subject': subject_name}
                found_subject_data_for_this_subject = False
                for score_type_display, col_name in details.items():
                    score_value = student_data_dict.get(col_name, 'N/A')
                    subject_info[score_type_display] = score_value
                    if score_value not in ['N/A', 'Column Missing', '', None]: # Check if value is meaningful
                        found_subject_data_for_this_subject = True
                        has_data_for_semester = True
                if found_subject_data_for_this_subject:
                    elective_subjects_data_list.append(subject_info)

        if has_data_for_semester:
            display_results['Semesters'][semester_key] = {
                'Core Subjects': core_subjects_data_list,
                'Elective Subjects': elective_subjects_data_list
            }
        else:
            display_results['Semesters'][semester_key] = 'Not Available'

    # Render the HTML template designed for PDF
    rendered_html = render_template('student_result_pdf.html', student_data=display_results, now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

    # Generate PDF from the rendered HTML
    pdf = HTML(string=rendered_html).write_pdf()

    # Create a Flask response with the PDF
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    filename = f"{display_results['Student Name'].replace(' ', '_')}_Results.pdf"
    response.headers['Content-Disposition'] = f'inline; filename={filename}'

    return response

@app.route('/library', methods=['GET', 'POST'])
def library():
    """Library page for admin uploads and student access."""
    files = []
    try:
        # List only allowed files in the uploads directory
        files = [f for f in os.listdir(app.config['UPLOAD_FOLDER']) if os.path.isfile(os.path.join(app.config['UPLOAD_FOLDER'], f)) and allowed_file(f)]
    except Exception as e:
        print(f"Error listing files in library: {e}")
        flash(f"Error loading library files: {e}", 'danger')

    # Check if admin is logged in for upload functionality
    is_admin = session.get('admin_logged_in', False)

    if request.method == 'POST':
        # Only process upload if admin is logged in
        if not is_admin:
            flash('You do not have permission to upload files.', 'danger')
            return redirect(url_for('library'))

        # Handle file upload
        if 'file' not in request.files:
            flash('No file part in the request.', 'warning')
            return redirect(url_for('library'))

        file = request.files['file']

        if file.filename == '':
            flash('No selected file.', 'warning')
            return redirect(url_for('library'))

        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            try:
                file.save(filepath)
                flash(f'File "{filename}" uploaded successfully.', 'success')
            except Exception as e:
                print(f"Error saving file {filename}: {e}")
                flash(f'Error uploading file "{filename}": {e}', 'danger')
        else:
            flash('Invalid file type. Allowed types are: pdf, doc, docx, xls, xlsx.', 'warning')

        # Redirect back to the library page after upload
        return redirect(url_for('library'))

    # Handle GET request
    return render_template('library.html', files=files, is_admin=is_admin)

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """Serve uploaded files."""
    try:
        # Ensure the requested file is within the allowed extensions before serving
        if not allowed_file(filename):
             return "File type not allowed.", 403 # Forbidden

        return send_from_directory(app.config['UPLOAD_FOLDER'], filename)
    except FileNotFoundError:
        return "File not found.", 404


# ============================================================
# NEW MODULE ROUTES: STORE, FINANCE, ESTATE
# ============================================================

# --- STORE MODULE ROUTES ---

@app.route('/admin/store')
def admin_store():
    """Store management dashboard - reads from Google Sheets (online mode)."""
    if not session.get('admin_logged_in') and not session.get('store_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Try to read from Google Sheets first (online mode)
    items = read_store_items_from_google_sheet()
    
    # Fall back to local Excel if Google Sheets fails
    if items is None:
        items = SchoolStoreItem.all()
        flash('Reading from local database (offline mode).', 'warning')
    
    # Get statistics - safely convert to float to handle corrupted data
    total_items = len(items)
    
    def safe_float(value, default=0):
        """Safely convert a value to float, returning default on failure."""
        try:
            result = float(value)
            return result if not isinstance(result, float) or not (result != result) else default  # NaN check
        except (ValueError, TypeError):
            return default
    
    low_stock = len([i for i in items if safe_float(i.get('quantity', 0)) <= safe_float(i.get('min_threshold', 0))])
    today_date = datetime.now().strftime('%Y-%m-%d')
    
    # Get recent transactions
    transactions = read_store_transactions_from_google_sheet()
    if transactions is None:
        transactions = SchoolStoreTransaction.all()
    
    today_transactions = len([t for t in transactions if today_date in str(t.get('created_at', ''))])
    recent_transactions = sorted(transactions, key=lambda x: x.get('created_at', ''), reverse=True)[:10]
    
    # Pre-fetch item data for template display
    items_dict = {item.get('id'): item for item in items}
    
    return render_template('admin_store.html',
                           total_items=total_items,
                           low_stock=low_stock,
                           today_transactions=today_transactions,
                           recent_transactions=recent_transactions,
                           items_dict=items_dict,
                           StoreItem=SchoolStoreItem)


@app.route('/admin/store/inventory')
def admin_store_inventory():
    """View and manage store inventory - reads from Google Sheets (online mode)."""
    if not session.get('admin_logged_in') and not session.get('store_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Try to read from Google Sheets first (online mode)
    items = read_store_items_from_google_sheet()
    
    # If Google Sheets fails, fall back to local Excel
    if items is None:
        items = sorted(SchoolStoreItem.all(), key=lambda x: (x.get('category', ''), x.get('name', '')))
        flash('Reading from local database (offline mode).', 'warning')
    
    return render_template('admin_store_inventory.html', items=items)


@app.route('/admin/store/add_item', methods=['GET', 'POST'])
def admin_store_add_item():
    """Add new item to store inventory - saves directly to Google Sheets (online mode)."""
    if not session.get('admin_logged_in') and not session.get('store_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    if request.method == 'POST':
        name = request.form.get('name')
        category = request.form.get('category')
        unit = request.form.get('unit')
        quantity = float(request.form.get('quantity', 0))
        min_threshold = float(request.form.get('min_threshold', 0))
        
        if not name or not category or not unit:
            flash('Please fill in all required fields.', 'warning')
        else:
            # Try to save directly to Google Sheets (online mode)
            success = save_store_item_to_google_sheet(
                name=name,
                category=category,
                unit=unit,
                quantity=quantity,
                min_threshold=min_threshold
            )
            
            if success:
                flash(f'Item "{name}" added successfully!', 'success')
            else:
                # Fallback: save to local Excel if Google Sheets fails
                SchoolStoreItem.add(
                    name=name,
                    category=category,
                    unit=unit,
                    quantity=quantity,
                    min_threshold=min_threshold
                )
                flash(f'Item "{name}" added to local database (offline mode).', 'warning')
            
            return redirect(url_for('admin_store_inventory'))
    
    return render_template('admin_store_add_item.html')


@app.route('/admin/store/restock/<item_id>', methods=['GET', 'POST'])
def admin_store_restock(item_id):
    """Restock an existing item - saves directly to Google Sheets (online mode)."""
    if not session.get('admin_logged_in') and not session.get('store_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Try to get item from Google Sheets first
    items = read_store_items_from_google_sheet()
    if items is None:
        # Fallback to local database
        items = SchoolStoreItem.all()
    
    item = None
    for itm in items:
        if str(itm.get('id', '')) == str(item_id):
            item = itm
            break
    
    if not item:
        abort(404)
    
    if request.method == 'POST':
        quantity = float(request.form.get('quantity', 0))
        recipient = request.form.get('recipient', '')
        notes = request.form.get('notes', '')
        
        if quantity <= 0:
            flash('Quantity must be greater than 0.', 'warning')
        else:
            # Safely convert quantity to float
            try:
                current_qty = float(item.get('quantity', 0))
            except (ValueError, TypeError):
                current_qty = 0
            new_quantity = current_qty + quantity
            
            # Update item quantity in Google Sheets (online mode)
            success = update_store_item_in_google_sheet(
                item_id=str(item_id),
                new_quantity=new_quantity
            )
            
            if success:
                # Also save the transaction to Google Sheets
                save_store_transaction_to_google_sheet(
                    item_id=str(item_id),
                    item_name=item.get('name', 'Unknown'),
                    transaction_type='IN',
                    quantity=quantity,
                    recipient=recipient,
                    notes=notes
                )
                flash(f'Successfully restocked {quantity} {item.get("unit", "")} of {item.get("name", "")}!', 'success')
            else:
                # Fallback to local database if Google Sheets fails
                item_id_int = int(item.get('id', 0))
                SchoolStoreItem.update(item_id_int, quantity=new_quantity, updated_at=datetime.now())
                transaction_data = {
                    'item_id': item_id_int,
                    'transaction_type': 'IN',
                    'quantity': quantity,
                    'recipient': recipient,
                    'recipient_type': 'Supplier',
                    'notes': notes,
                    'issued_by': session.get('admin_username', session.get('staff_username', 'Admin'))
                }
                SchoolStoreTransaction.add(**transaction_data)
                flash(f'Successfully restocked {quantity} {item.get("unit", "")} of {item.get("name", "")} (offline mode)!', 'warning')
            
            return redirect(url_for('admin_store_inventory'))
    
    return render_template('admin_store_restock.html', item=item)


@app.route('/admin/store/issue', methods=['GET', 'POST'])
def admin_store_issue():
    """Issue items from store - saves directly to Google Sheets (online mode)."""
    if not session.get('admin_logged_in') and not session.get('store_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Try to get items from Google Sheets first
    items = read_store_items_from_google_sheet()
    if items is None:
        # Fallback to local database
        items = sorted(SchoolStoreItem.all(), key=lambda x: x.get('name', ''))
    
    if request.method == 'POST':
        item_id = request.form.get('item_id')
        quantity = float(request.form.get('quantity', 0))
        recipient = request.form.get('recipient')
        recipient_type = request.form.get('recipient_type')
        notes = request.form.get('notes', '')
        
        # Find the item
        item = None
        for itm in items:
            if str(itm.get('id', '')) == str(item_id):
                item = itm
                break
        
        if not item:
            flash('Item not found.', 'danger')
            return render_template('admin_store_issue.html', items=items)
        elif quantity <= 0:
            flash('Quantity must be greater than 0.', 'warning')
            return render_template('admin_store_issue.html', items=items)
        else:
            # Safely convert quantity to float
            try:
                current_qty = float(item.get('quantity', 0))
            except (ValueError, TypeError):
                current_qty = 0
            
            if current_qty < quantity:
                flash(f'Insufficient stock! Available: {current_qty} {item.get("unit", "")}', 'danger')
                return render_template('admin_store_issue.html', items=items)
            
            new_quantity = current_qty - quantity
            
            # Update item quantity in Google Sheets (online mode)
            success = update_store_item_in_google_sheet(
                item_id=str(item_id),
                new_quantity=new_quantity
            )
            
            if success:
                # Also save the transaction to Google Sheets
                save_store_transaction_to_google_sheet(
                    item_id=str(item_id),
                    item_name=item.get('name', 'Unknown'),
                    transaction_type='OUT',
                    quantity=quantity,
                    recipient=recipient,
                    notes=f"{recipient_type}: {recipient} - {notes}"
                )
                flash(f'Successfully issued {quantity} {item.get("unit", "")} of {item.get("name", "")} to {recipient}!', 'success')
            else:
                # Fallback to local database if Google Sheets fails
                item_id_int = int(item.get('id', 0))
                SchoolStoreItem.update(item_id_int, quantity=new_quantity, updated_at=datetime.now())
                transaction_data = {
                    'item_id': item_id_int,
                    'transaction_type': 'OUT',
                    'quantity': quantity,
                    'recipient': recipient,
                    'recipient_type': recipient_type,
                    'notes': notes,
                    'issued_by': session.get('admin_username', session.get('staff_username', 'Admin'))
                }
                SchoolStoreTransaction.add(**transaction_data)
                flash(f'Successfully issued {quantity} {item.get("unit", "")} of {item.get("name", "")} to {recipient} (offline mode)!', 'warning')
            
            return redirect(url_for('admin_store'))
    
    # Sort items by name for display
    if items and isinstance(items, list) and len(items) > 0:
        if isinstance(items[0], dict):
            items = sorted(items, key=lambda x: x.get('name', ''))
    
    return render_template('admin_store_issue.html', items=items)


@app.route('/admin/store/cleanup_data')
def admin_store_cleanup_data():
    """Clean up corrupted data in the store items Google Sheet."""
    if not session.get('admin_logged_in') and not session.get('store_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    fixed_count = cleanup_store_items_data()
    
    if fixed_count >= 0:
        flash(f'Data cleanup complete! Fixed {fixed_count} rows with swapped quantity/unit values.', 'success')
    else:
        flash('Data cleanup failed. Check console for errors.', 'danger')
    
    return redirect(url_for('admin_store_inventory'))


@app.route('/admin/store/print_issue_report')
def admin_store_print_issue_report():
    """Print stock issued report showing remaining quantities and recipients."""
    if not session.get('admin_logged_in') and not session.get('store_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Get start and end date filters from query params (optional)
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    
    # Get all transactions from Google Sheets
    transactions = read_store_transactions_from_google_sheet()
    
    if transactions is None:
        # Fallback to local database
        transactions = SchoolStoreTransaction.all()
        flash('Reading from local database (offline mode).', 'warning')
    
    # Filter only OUT (issue) transactions
    issued_transactions = [txn for txn in transactions if txn.get('transaction_type', '') == 'OUT']
    
    # Apply date filters if provided
    if start_date:
        issued_transactions = [txn for txn in issued_transactions 
                               if txn.get('created_at', '') >= start_date]
    if end_date:
        issued_transactions = [txn for txn in issued_transactions 
                               if txn.get('created_at', '')[:10] <= end_date]
    
    # Sort by date, newest first
    issued_transactions = sorted(issued_transactions, key=lambda x: x.get('created_at', ''), reverse=True)
    
    # Get current store items for remaining quantities
    store_items_list = read_store_items_from_google_sheet()
    if store_items_list is None:
        store_items_list = SchoolStoreItem.all()
    
    # Create a lookup dict for item details
    item_lookup = {}
    for item in store_items_list:
        try:
            qty = float(item.get('quantity', 0))
        except (ValueError, TypeError):
            qty = 0
        item_lookup[str(item.get('id', ''))] = {
            'name': item.get('name', 'Unknown'),
            'unit': item.get('unit', ''),
            'current_quantity': qty,
            'category': item.get('category', 'N/A')
        }
    
    # Attach item details and current remaining quantity to each transaction
    for txn in issued_transactions:
        item_id = str(txn.get('item_id', ''))
        if item_id in item_lookup:
            txn['item_name'] = item_lookup[item_id]['name']
            txn['item_unit'] = item_lookup[item_id]['unit']
            txn['item_category'] = item_lookup[item_id]['category']
            txn['current_remaining'] = item_lookup[item_id]['current_quantity']
        else:
            txn['item_name'] = txn.get('item_name', 'Unknown Item')
            txn['item_unit'] = ''
            txn['item_category'] = 'N/A'
            txn['current_remaining'] = 'N/A'
    
    # Calculate summary statistics
    total_issued = sum(float(txn.get('quantity', 0)) for txn in issued_transactions)
    unique_recipients = len(set(txn.get('recipient', '') for txn in issued_transactions if txn.get('recipient', '')))
    
    return render_template('admin_store_print_issue_report.html', 
                          transactions=issued_transactions,
                          total_issued=total_issued,
                          unique_recipients=unique_recipients,
                          start_date=start_date,
                          end_date=end_date,
                          report_date=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))


@app.route('/admin/store/transactions')
def admin_store_transactions():
    """View all store transactions - reads from Google Sheets (online mode)."""
    if not session.get('admin_logged_in') and not session.get('store_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Get transactions from Google Sheets (online mode) - sorted by newest first
    transactions = read_store_transactions_from_google_sheet()
    
    if transactions is None:
        # Fallback to local database
        transactions = sorted(SchoolStoreTransaction.all(), key=lambda x: x.get('created_at', ''), reverse=True)[:100]
        flash('Reading transactions from local database (offline mode).', 'warning')
    else:
        # Sort by created_at, newest first
        transactions = sorted(transactions, key=lambda x: x.get('created_at', ''), reverse=True)[:100]
    
    # Get item names from store items for display
    store_items_list = read_store_items_from_google_sheet()
    if store_items_list is None:
        store_items_list = SchoolStoreItem.all()
    
    store_items = {str(item.get('id', '')): item.get('name', 'Unknown') for item in store_items_list}
    
    # Attach item names to transactions
    for txn in transactions:
        item_id = str(txn.get('item_id', ''))
        if item_id in store_items:
            txn['item_name'] = store_items[item_id]
        else:
            txn['item_name'] = txn.get('item_name', 'Unknown Item')
    
    return render_template('admin_store_transactions.html', transactions=transactions)

@app.route('/admin/store/edit_item/<item_id>', methods=['GET', 'POST'])
def admin_store_edit_item(item_id):
    """Edit an existing item in the store inventory."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    item = SchoolStoreItem.get_by_id(item_id)
    if not item:
        flash('Item not found.', 'danger')
        return redirect(url_for('admin_store_inventory'))
    
    if request.method == 'POST':
        name = request.form.get('name')
        category = request.form.get('category')
        unit = request.form.get('unit')
        quantity = float(request.form.get('quantity', 0))
        min_threshold = float(request.form.get('min_threshold', 0))
        
        if not name or not category or not unit:
            flash('Please fill in all required fields.', 'warning')
        else:
            SchoolStoreItem.update(item_id,
                name=name,
                category=category,
                unit=unit,
                quantity=quantity,
                min_threshold=min_threshold
            )
            flash(f'Item "{name}" updated successfully!', 'success')
            return redirect(url_for('admin_store_inventory'))
    
    return render_template('admin_store_edit_item.html', item=item)


@app.route('/admin/store/delete_item/<item_id>', methods=['GET', 'POST'])
def admin_store_delete_item(item_id):
    """Delete an item from the store inventory."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    item = SchoolStoreItem.get_by_id(item_id)
    if not item:
        flash('Item not found.', 'danger')
        return redirect(url_for('admin_store_inventory'))
    
    if request.method == 'POST':
        item_name = item.get('name', 'Unknown')
        SchoolStoreItem.delete(item_id)
        flash(f'Item "{item_name}" deleted successfully!', 'success')
        return redirect(url_for('admin_store_inventory'))
    
    # Show confirmation page
    return render_template('admin_store_delete_item.html', item=item)

# =============================================================================
# STORE DATA SYNC ROUTES - Import/Export/Google Sheets Sync
# =============================================================================

@app.route('/admin/store/sync')
def admin_store_sync():
    """Store data sync management page."""
    if not session.get('admin_logged_in') and not session.get('store_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Get current status
    local_items = SchoolStoreItem.all()
    local_count = len(local_items)
    
    # Get Google Sheet info
    google_sheet_url = STORE_INVENTORY_SHEET_URL if STORE_INVENTORY_SHEET_URL else None
    
    return render_template('admin_store_sync.html', 
                           local_count=local_count,
                           google_sheet_url=google_sheet_url,
                           has_google_sheet=bool(STORE_INVENTORY_SHEET_URL),
                           store_items_sheet_id=STORE_ITEMS_SHEET_ID if 'STORE_ITEMS_SHEET_ID' in dir() else None)


@app.route('/admin/database/sync')
def admin_database_sync():
    """Unified database sync management page - syncs ALL data from unified Google Sheet."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Get status of all local databases
    db_status = {}
    for data_type in EXCEL_FILES.keys():
        excel_path = get_excel_path(data_type)
        if os.path.exists(excel_path):
            try:
                df = pd.read_excel(excel_path)
                db_status[data_type] = {'exists': True, 'count': len(df)}
            except:
                db_status[data_type] = {'exists': True, 'count': 0}
        else:
            db_status[data_type] = {'exists': False, 'count': 0}
    
    return render_template('admin_database_sync.html',
                           db_status=db_status,
                           unified_sheet_id=UNIFIED_GOOGLE_SHEET_ID if UNIFIED_SHEET_ENABLED else None,
                           sheet_workbooks=SHEET_WORKBOOKS)


@app.route('/admin/sync_all_data')
def admin_sync_all_data():
    """Simple one-click sync page to download all data from Google Sheets to local Excel."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    return render_template('admin_sync_all_data.html')


@app.route('/api/database/sync/all', methods=['POST'])
def api_database_sync_all():
    """Sync ALL data types from unified Google Sheet to local Excel files."""
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Not authorized'}), 401
    
    if not UNIFIED_SHEET_ENABLED:
        return jsonify({'success': False, 'message': 'Unified sheet sync is disabled'}), 400
    
    try:
        results = sync_all_data_from_unified_sheet()
        
        if 'error' in results:
            return jsonify({'success': False, 'message': results['error']}), 500
        
        total_synced = sum(r.get('count', 0) for r in results.values() if r.get('success'))
        failed = [k for k, v in results.items() if not v.get('success') and 'message' not in v]
        
        return jsonify({
            'success': True,
            'message': f'Synced {total_synced} total records across all databases',
            'results': results,
            'total_synced': total_synced,
            'failed_count': len(failed)
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/database/sync/<data_type>', methods=['POST'])
def api_database_sync_single(data_type):
    """Sync a specific data type from unified Google Sheet."""
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Not authorized'}), 401
    
    if data_type not in SHEET_WORKBOOKS:
        return jsonify({'success': False, 'message': f'Unknown data type: {data_type}'}), 400
    
    try:
        df = sync_data_from_unified_sheet(data_type)
        
        if df is not None:
            return jsonify({
                'success': True,
                'message': f'Synced {len(df)} records to {data_type}',
                'count': len(df)
            })
        else:
            return jsonify({
                'success': False,
                'message': f'Failed to sync {data_type} - worksheet may not exist or be empty'
            }), 400
            
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/database/push/<data_type>', methods=['POST'])
def api_database_push_single(data_type):
    """Push local data to unified Google Sheet."""
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Not authorized'}), 401
    
    result = push_data_to_unified_sheet(data_type)
    
    if result.get('success'):
        return jsonify(result)
    else:
        return jsonify(result), 400


@app.route('/api/database/push/all', methods=['POST'])
def api_database_push_all():
    """Push ALL local data to unified Google Sheet."""
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Not authorized'}), 401
    
    results = {}
    
    for data_type in EXCEL_FILES.keys():
        result = push_data_to_unified_sheet(data_type)
        results[data_type] = result
    
    total_success = sum(1 for r in results.values() if r.get('success'))
    
    return jsonify({
        'success': total_success > 0,
        'message': f'Successfully pushed {total_success} of {len(results)} databases',
        'results': results
    })


@app.route('/api/store/sync/pull', methods=['POST'])
def api_store_sync_pull():
    """Pull store data from Google Sheet to local Excel."""
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Not authorized'}), 401
    
    if not STORE_INVENTORY_SHEET_URL:
        return jsonify({'success': False, 'message': 'Google Sheet URL not configured'}), 400
    
    try:
        # Read from Google Sheet
        df = pd.read_csv(STORE_INVENTORY_SHEET_URL)
        
        if df.empty:
            return jsonify({'success': False, 'message': 'Google Sheet is empty'}), 400
        
        # Clean up column names
        df.columns = df.columns.str.strip()
        
        # Ensure required columns exist (accept both 'unit' and 'unit_price')
        required_cols = ['id', 'name', 'category', 'quantity', 'min_threshold']
        missing_cols = [col for col in required_cols if col not in df.columns]
        if missing_cols:
            return jsonify({'success': False, 'message': f'Missing columns: {missing_cols}. Please ensure your Google Sheet has columns: id, name, category, quantity, unit/unit_price, min_threshold'}), 400
        
        # Rename 'unit' to 'unit_price' if it exists for consistency with local Excel
        if 'unit' in df.columns and 'unit_price' not in df.columns:
            df = df.rename(columns={'unit': 'unit_price'})
        
        # Add created_at column if not present
        if 'created_at' not in df.columns:
            df['created_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Save to local Excel
        excel_path = get_excel_path('store_items')
        df.to_excel(excel_path, index=False)
        
        return jsonify({
            'success': True,
            'message': f'Successfully synced {len(df)} items from Google Sheet to local Excel',
            'items_synced': len(df)
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error pulling from Google Sheet: {str(e)}'}), 500
        
        # Also sync transactions if you have a Google Sheet for it
        if 'STORE_TRANSACTIONS_SHEET_URL' in globals() and STORE_TRANSACTIONS_SHEET_URL:
            try:
                df_txn = pd.read_csv(STORE_TRANSACTIONS_SHEET_URL)
                if not df_txn.empty:
                    df_txn.columns = df_txn.columns.str.strip()
                    df_txn.to_excel(get_excel_path('store_transactions'), index=False)
                    txn_count = len(df_txn)
                else:
                    txn_count = 0
            except:
                txn_count = 0
        else:
            txn_count = 0
        
        return jsonify({
            'success': True,
            'message': f'Successfully synced {len(df)} items from Google Sheet',
            'items_synced': len(df),
            'transactions_synced': txn_count
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/store/sync/push', methods=['POST'])
def api_store_sync_push():
    """Push local store data directly to Google Sheet using gspread API."""
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Not authorized'}), 401
    
    if not STORE_ITEMS_SHEET_ID:
        return jsonify({'success': False, 'message': 'Google Sheet ID not configured. Please set STORE_ITEMS_SHEET_ID'}), 400
    
    try:
        # Get local data
        items = SchoolStoreItem.all()
        
        if not items:
            return jsonify({'success': False, 'message': 'No local data to push to Google Sheet'}), 400
        
        # Get Google Sheets client using service account
        gc = get_google_sheet_client()
        if not gc:
            return jsonify({'success': False, 'message': 'Failed to authenticate with Google Sheets. Check service_account_credentials.json file.'}), 500
        
        # Open the Google Sheet by ID
        try:
            spreadsheet = gc.open_by_key(STORE_ITEMS_SHEET_ID)
        except gspread.exceptions.SpreadsheetNotFound:
            return jsonify({'success': False, 'message': 'Google Sheet not found. Check STORE_ITEMS_SHEET_ID'}), 400
        
        # Get or create the worksheet
        try:
            worksheet = spreadsheet.worksheet(STORE_ITEMS_SHEET_NAME)
        except gspread.exceptions.WorksheetNotFound:
            # Create worksheet if it doesn't exist
            worksheet = spreadsheet.add_worksheet(STORE_ITEMS_SHEET_NAME, rows=1000, cols=20)
        
        # Prepare data for Google Sheets
        df = pd.DataFrame(items)
        
        # Convert DataFrame to list of lists for gspread
        # Header row first
        headers = df.columns.tolist()
        data_rows = df.values.tolist()
        
        # Combine headers and data
        all_values = [headers] + data_rows
        
        # Clear existing content and update with new data
        worksheet.clear()
        worksheet.update(values=all_values, range_name='A1')
        
        return jsonify({
            'success': True,
            'message': f'Successfully pushed {len(items)} items directly to Google Sheet',
            'items_synced': len(items),
            'sheet_url': f'https://docs.google.com/spreadsheets/d/{STORE_ITEMS_SHEET_ID}'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error pushing to Google Sheet: {str(e)}'}), 500


@app.route('/api/store/export/csv')
def api_store_export_csv():
    """Export store data as CSV for manual upload to Google Sheets."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    try:
        # Get local data
        items = SchoolStoreItem.all()
        
        if not items:
            flash('No local data to export', 'warning')
            return redirect(url_for('admin_store_sync'))
        
        # Convert to DataFrame
        df = pd.DataFrame(items)
        
        # Generate CSV download
        output = io.BytesIO()
        df.to_csv(output, index=False)
        output.seek(0)
        
        response = make_response(output.getvalue())
        response.headers['Content-Type'] = 'text/csv'
        response.headers['Content-Disposition'] = f'attachment; filename=store_items_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        return response
        
    except Exception as e:
        flash(f'Error exporting CSV: {str(e)}', 'danger')
        return redirect(url_for('admin_store_sync'))


@app.route('/api/store/export/excel', methods=['GET'])
def api_store_export_excel():
    """Export store data as Excel file for download."""
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Not authorized'}), 401
    
    try:
        # Get local data
        items = SchoolStoreItem.all()
        transactions = SchoolStoreTransaction.all()
        
        # Create Excel file in memory
        output = io.BytesIO()
        
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            # Write items sheet
            if items:
                df_items = pd.DataFrame(items)
                df_items.to_excel(writer, sheet_name='Store Items', index=False)
            
            # Write transactions sheet
            if transactions:
                df_txn = pd.DataFrame(transactions)
                df_txn.to_excel(writer, sheet_name='Transactions', index=False)
        
        output.seek(0)
        
        response = make_response(output.getvalue())
        response.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        response.headers['Content-Disposition'] = f'attachment; filename=store_data_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        
        return response
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/store/import', methods=['POST'])
def api_store_import():
    """Import store data from uploaded CSV or Excel file."""
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Not authorized'}), 401
    
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'No file uploaded'}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({'success': False, 'message': 'No file selected'}), 400
    
    try:
        # Determine file type
        filename = file.filename.lower()
        
        if filename.endswith('.csv'):
            df = pd.read_csv(file)
        elif filename.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(file)
        else:
            return jsonify({'success': False, 'message': 'Unsupported file type. Use CSV or Excel.'}), 400
        
        if df.empty:
            return jsonify({'success': False, 'message': 'File is empty'}), 400
        
        # Clean column names
        df.columns = df.columns.str.strip()
        
        # Ensure required columns
        required_cols = ['name', 'category', 'quantity']
        missing = [col for col in required_cols if col not in df.columns]
        if missing:
            return jsonify({'success': False, 'message': f'Missing required columns: {missing}'}), 400
        
        # Add missing columns with defaults if needed
        if 'unit' not in df.columns:
            df['unit'] = 'pieces'
        if 'min_threshold' not in df.columns:
            df['min_threshold'] = 0
        if 'created_at' not in df.columns:
            df['created_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if 'id' not in df.columns:
            # Get next ID
            existing = SchoolStoreItem.all()
            max_id = max([int(i.get('id', 0)) for i in existing], default=0)
            df['id'] = range(max_id + 1, max_id + 1 + len(df))
        
        # Save to local Excel
        excel_path = get_excel_path('store_items')
        df.to_excel(excel_path, index=False)
        
        return jsonify({
            'success': True,
            'message': f'Successfully imported {len(df)} items',
            'items_imported': len(df)
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/store/transactions/sync/pull', methods=['POST'])
def api_store_transactions_sync_pull():
    """Pull store transactions from Google Sheet to local Excel."""
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Not authorized'}), 401
    
    if not globals().get('STORE_TRANSACTIONS_SHEET_URL'):
        return jsonify({'success': False, 'message': 'Transactions Google Sheet URL not configured'}), 400
    
    try:
        df = pd.read_csv(globals()['STORE_TRANSACTIONS_SHEET_URL'])
        
        if df.empty:
            return jsonify({'success': False, 'message': 'Google Sheet is empty'}), 400
        
        df.columns = df.columns.str.strip()
        df.to_excel(get_excel_path('store_transactions'), index=False)
        
        return jsonify({
            'success': True,
            'message': f'Successfully synced {len(df)} transactions from Google Sheet',
            'transactions_synced': len(df)
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# --- API ROUTES ---

@app.route('/api/students/search')
def api_students_search():
    """API endpoint to search students"""
    query = request.args.get('q', '')
    students = SchoolStudent.search(query)
    
    # Format for JSON response
    results = []
    for s in students:
        results.append({
            'id': s.get('student_id'),
            'student_id': s.get('student_id'),
            'name': f"{s.get('first_name', '')} {s.get('last_name', '')}".strip(),
            'first_name': s.get('first_name', ''),
            'last_name': s.get('last_name', ''),
            'class': s.get('class', ''),
            'section': s.get('section', ''),
            'gender': s.get('gender', ''),
            'parent_phone': s.get('parent_phone', ''),
            'parent_name': s.get('parent_name', '')
        })
    
    return jsonify({'students': results})


@app.route('/api/student/<student_id>')
def api_student_get(student_id):
    """API endpoint to get a specific student"""
    student = SchoolStudent.get_by_student_id(student_id)
    
    if not student:
        return jsonify({'error': 'Student not found'}), 404
    
    # Handle different field names - try student_name first, then first_name/last_name
    student_name = student.get('student_name', '')
    if not student_name:
        student_name = f"{student.get('first_name', '')} {student.get('last_name', '')}".strip()
    
    return jsonify({
        'student_id': student.get('student_id'),
        'name': student_name,
        'first_name': student.get('first_name', student_name.split()[0] if student_name else ''),
        'last_name': student.get('last_name', ' '.join(student_name.split()[1:]) if student_name else ''),
        'class': student.get('class', student.get('student_class', '')),
        'section': student.get('section', ''),
        'gender': student.get('gender', ''),
        'date_of_birth': student.get('date_of_birth', ''),
        'parent_name': student.get('parent_name', student.get('father_name', '')),
        'parent_phone': student.get('parent_phone', ''),
        'parent_email': student.get('parent_email', ''),
        'address': student.get('address', '')
    })


# --- FINANCE MODULE ROUTES ---

@app.route('/admin/finance')
def admin_finance():
    """Finance management dashboard."""
    if not check_finance_access():
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Get statistics
    total_revenue = sum(p.get('amount', 0) for p in SchoolPayment.all())
    total_expenses = sum(e.get('amount', 0) for e in SchoolExpense.all())
    balance = total_revenue - total_expenses
    
    # Get debtors count and total outstanding
    debtors_count = len([a for a in SchoolStudentAccount.all() if a.get('balance', 0) > 0])
    total_outstanding = sum(a.get('balance', 0) for a in SchoolStudentAccount.all() if a.get('balance', 0) > 0)
    
    recent_payments = sorted(SchoolPayment.all(), key=lambda x: x.get('created_at', ''), reverse=True)[:10]
    
    # Check if Google Sheets is configured
    google_sheet_url = globals().get('PAYMENTS_SHEET_URL', '')
    has_google_sheet = bool(google_sheet_url)
    
    return render_template('admin_finance.html',
                           total_revenue=total_revenue,
                           total_expenses=total_expenses,
                           balance=balance,
                           debtors_count=debtors_count,
                           total_outstanding=total_outstanding,
                           recent_payments=recent_payments,
                           has_google_sheet=has_google_sheet)

@app.route('/estate/dashboard')
def estate_dashboard():
    if not session.get('estate_logged_in'):
        flash('Please login to access the Estate dashboard.', 'warning')
        return redirect(url_for('estate_login'))
    
    # Get statistics
    total_assets = SchoolAsset.count()
    active_assets = len(SchoolAsset.filter_by(status='Active'))
    pending_maintenance = len([m for m in SchoolMaintenanceRequest.all() if m.get('status') in ['Reported', 'In Progress']])
    
    return render_template('estate_dashboard.html',
                           total_assets=total_assets,
                           active_assets=active_assets,
                           pending_maintenance=pending_maintenance,
                           total_locations=len(SchoolLocation.all()))

@app.route('/estate/assets', methods=['GET', 'POST'])
def estate_assets():
    """Manage estate assets - Estate Officer access."""
    if not session.get('estate_logged_in'):
        flash('Please login to access the Estate dashboard.', 'warning')
        return redirect(url_for('estate_login'))
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'add':
            SchoolAsset.add(
                name=request.form.get('name'),
                asset_code=request.form.get('asset_code'),
                category=request.form.get('category'),
                location_id=request.form.get('location_id'),
                status='Active',
                condition=request.form.get('condition', 'Good'),
                purchase_date=request.form.get('purchase_date'),
                created_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            )
            flash('Asset added successfully!', 'success')
        elif action == 'delete':
            asset_id = request.form.get('asset_id')
            SchoolAsset.delete(asset_id)
            flash('Asset deleted successfully!', 'success')
    
    assets = sorted(SchoolAsset.all(), key=lambda x: x.get('name', ''))
    locations = SchoolLocation.all()
    return render_template('admin_estate_assets.html', assets=assets, locations=locations)

@app.route('/estate/locations', methods=['GET', 'POST'])
def estate_locations():
    """Manage locations - Estate Officer access."""
    if not session.get('estate_logged_in'):
        flash('Please login to access the Estate dashboard.', 'warning')
        return redirect(url_for('estate_login'))
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'add':
            SchoolLocation.add(
                name=request.form.get('name'),
                location_type=request.form.get('location_type'),
                description=request.form.get('description', ''),
                created_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            )
            flash('Location added successfully!', 'success')
    
    locations = sorted(SchoolLocation.all(), key=lambda x: x.get('name', ''))
    return render_template('admin_estate_locations.html', locations=locations)

@app.route('/estate/maintenance', methods=['GET', 'POST'])
def estate_maintenance():
    """Manage maintenance requests - Estate Officer access."""
    if not session.get('estate_logged_in'):
        flash('Please login to access the Estate dashboard.', 'warning')
        return redirect(url_for('estate_login'))
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'add':
            SchoolMaintenanceRequest.add(
                asset_id=request.form.get('asset_id'),
                asset_name=request.form.get('asset_name', ''),
                issue_type=request.form.get('issue_type'),
                description=request.form.get('description'),
                priority=request.form.get('priority', 'Medium'),
                status='Reported',
                reported_by=session.get('staff_username', 'Estate Officer'),
                reported_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            )
            flash('Maintenance request submitted!', 'success')
        elif action == 'update':
            request_id = request.form.get('request_id')
            SchoolMaintenanceRequest.update(request_id, status=request.form.get('status'))
            flash('Maintenance status updated!', 'success')
    
    assets = SchoolAsset.all()
    maintenance_requests = sorted(SchoolMaintenanceRequest.all(), key=lambda x: x.get('reported_at', ''), reverse=True)
    return render_template('admin_estate_maintenance.html', maintenance_requests=maintenance_requests, assets=assets)

@app.route('/estate/logout')
def estate_logout():
    """Logout from estate dashboard."""
    session.pop('estate_logged_in', None)
    session.pop('staff_username', None)
    session.pop('staff_role', None)
    flash('You have been logged out from Estate dashboard.', 'info')
    return redirect(url_for('estate_login'))

@app.route('/store/dashboard')
def store_dashboard():
    """Redirect store staff to the admin store dashboard."""
    if not session.get('store_logged_in'):
        flash('Please login to access the Store dashboard.', 'warning')
        return redirect(url_for('store_login'))
    
    # Redirect to admin_store which now supports store_logged_in users
    return redirect(url_for('admin_store'))

@app.route('/store/logout')
def store_logout():
    """Logout from store dashboard."""
    session.pop('store_logged_in', None)
    session.pop('staff_username', None)
    session.pop('staff_role', None)
    flash('You have been logged out from Store dashboard.', 'info')
    return redirect(url_for('store_login'))

@app.route('/admin/finance/fee_setup', methods=['GET', 'POST'])
def admin_finance_fee_setup():
    """Setup fee types and amounts."""
    if not check_finance_access():
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description', '')
        amount = float(request.form.get('amount', 0))
        academic_year = request.form.get('academic_year')
        
        if not name or not amount or not academic_year:
            flash('Please fill in all required fields.', 'warning')
        else:
            SchoolFeeType.add(
                name=name,
                description=description,
                amount=amount,
                academic_year=academic_year,
                is_active=True,
                created_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            )
            flash(f'Fee type "{name}" created successfully!', 'success')
            return redirect(url_for('admin_finance_fee_setup'))
    
    fees = sorted(SchoolFeeType.all(), key=lambda x: (x.get('academic_year', ''), x.get('name', '')))
    
    # Check for sync from Google Sheets
    fee_types_sheet_url = globals().get('FEE_TYPES_SHEET_URL', '')
    show_sync_notice = bool(fee_types_sheet_url)
    
    return render_template('admin_finance_fee_setup.html', 
                            fees=fees, 
                            show_sync_notice=show_sync_notice,
                            sheet_url=fee_types_sheet_url)


@app.route('/admin/finance/payment', methods=['GET', 'POST'])
def admin_finance_payment():
    """Record payment from student."""
    if not check_finance_access():
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    if request.method == 'POST':
        student_id = request.form.get('student_id')
        student_name = request.form.get('student_name')
        fee_type_id = request.form.get('fee_type_id')
        amount = float(request.form.get('amount', 0))
        payment_method = request.form.get('payment_method')
        reference_number = request.form.get('reference_number', '')
        notes = request.form.get('notes', '')
        
        if not student_id or not student_name or not fee_type_id or not amount or not payment_method:
            flash('Please fill in all required fields.', 'warning')
        else:
            # Record payment - use dictionary format for ExcelModel
            payment_data = {
                'student_id': student_id,
                'student_name': student_name,
                'fee_type_id': fee_type_id,
                'amount': amount,
                'payment_method': payment_method,
                'reference_number': reference_number,
                'received_by': session.get('admin_username', 'Admin'),
                'notes': notes
            }
            SchoolPayment.add(**payment_data)
            
            # Sync payment to Google Sheet
            sync_payment_to_google_sheet(payment_data)
            
            # Update student account
            accounts = SchoolStudentAccount.filter_by(student_id=student_id)
            account = accounts[0] if accounts else None
            if account:
                account['total_paid'] = account.get('total_paid', 0) + amount
                account['balance'] = account.get('balance', 0) - amount
                account['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                SchoolStudentAccount.update(account['id'], **account)
            else:
                # Create new account if doesn't exist
                account_data = {
                    'student_id': student_id,
                    'student_name': student_name,
                    'total_paid': amount,
                    'balance': -amount
                }
                SchoolStudentAccount.add(**account_data)
            
            flash(f'Payment of GHS {amount} recorded for {student_name}!', 'success')
            return redirect(url_for('admin_finance'))
    
    fees = SchoolFeeType.filter_by(is_active=True)
    return render_template('admin_finance_payment.html', fees=fees)


@app.route('/admin/finance/students', methods=['GET', 'POST'])
def admin_finance_students():
    """Display student records for payment and history actions with balance tracking."""
    if not check_finance_access():
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Get search and filter parameters
    search_query = request.args.get('search', '')
    filter_by = request.args.get('filter', '')
    dept_filter = request.args.get('department', '')
    edit_student = None
    
    # Handle POST requests for adding, updating, or deleting students
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'add_student':
            # Add new student
            student_id = request.form.get('student_id')
            name = request.form.get('name')
            father_name = request.form.get('father_name')
            parent_phone = request.form.get('parent_phone')
            department = request.form.get('department')
            student_class = request.form.get('student_class', request.form.get('class', ''))
            
            if not student_id or not name:
                flash('Student ID and Name are required.', 'warning')
            else:
                # Check if student already exists
                existing = SchoolStudent.get_by_student_id(student_id)
                if existing:
                    flash(f'Student with ID {student_id} already exists.', 'warning')
                else:
                    # Add the student with simplified fields for finance
                    SchoolStudent.add(
                        student_id=student_id,
                        student_name=name,
                        father_name=father_name or '',
                        parent_phone=parent_phone or '',
                        department=department or '',
                        student_class=student_class or ''
                    )
                    flash(f'Student "{name}" added successfully!', 'success')
            
            return redirect(url_for('admin_finance_students'))
        
        elif action == 'assign_fees':
            # Assign fees to a student
            student_id = request.form.get('student_id')
            fee_amount = float(request.form.get('total_fees', 0))
            
            if student_id:
                existing = SchoolStudent.get_by_student_id(student_id)
                if existing:
                    SchoolStudent.update(existing.get('id'), total_fees=fee_amount)
                    flash(f'Fees of GHS {fee_amount:.2f} assigned successfully!', 'success')
                else:
                    flash(f'Student with ID {student_id} not found.', 'warning')
            
            return redirect(url_for('admin_finance_students'))
        
        elif action == 'update':
            # Update existing student
            student_id = request.form.get('student_id')
            name = request.form.get('name')
            father_name = request.form.get('father_name')
            parent_phone = request.form.get('parent_phone')
            department = request.form.get('department')
            student_class = request.form.get('student_class', request.form.get('class', ''))
            total_fees = float(request.form.get('total_fees', 0))
            
            if not student_id or not name:
                flash('Student ID and Name are required.', 'warning')
            else:
                # Get existing student to get the internal ID
                existing = SchoolStudent.get_by_student_id(student_id)
                if existing:
                    # Update the student
                    SchoolStudent.update(existing.get('id'), 
                        student_name=name,
                        father_name=father_name or '',
                        parent_phone=parent_phone or '',
                        department=department or '',
                        student_class=student_class or '',
                        total_fees=total_fees
                    )
                    flash(f'Student "{name}" updated successfully!', 'success')
                else:
                    flash(f'Student with ID {student_id} not found.', 'warning')
            
            return redirect(url_for('admin_finance_students'))
        
        elif action == 'delete':
            # Delete student
            student_id = request.form.get('student_id')
            if student_id:
                existing = SchoolStudent.get_by_student_id(student_id)
                if existing:
                    SchoolStudent.delete(existing.get('id'))
                    flash(f'Student "{existing.get("student_name")}" deleted successfully!', 'success')
                else:
                    flash(f'Student with ID {student_id} not found.', 'warning')
            
            return redirect(url_for('admin_finance_students'))
    
    # Handle edit parameter
    edit_id = request.args.get('edit')
    if edit_id:
        edit_student = SchoolStudent.get_by_student_id(edit_id)
        if not edit_student:
            flash(f'Student with ID {edit_id} not found.', 'warning')
    
    # Get all students
    students = SchoolStudent.search(search_query) if search_query else SchoolStudent.all()
    
    # Debug: Print student count
    print(f"[DEBUG] Found {len(students)} students in database")
    
    # Calculate balance for each student (total_fees - total_paid)
    for student in students:
        student_id = student.get('student_id') or student.get('Student ID') or student.get('id', '')
        
        # Get total_fees (default to 0 if not set)
        total_fees = float(student.get('total_fees', 0) or 0)
        
        # Calculate total paid from payments
        student_payments = [p for p in SchoolPayment.all() 
                           if str(p.get('student_id', '')).strip() == str(student_id).strip()]
        total_paid = sum(p.get('amount', 0) for p in student_payments)
        
        # Calculate balance
        balance = total_fees - total_paid
        
        # Update student dict with calculated values
        student['total_fees'] = total_fees
        student['total_paid'] = total_paid
        student['balance'] = balance
        student['payment_count'] = len(student_payments)
    
    # Filter by outstanding/paid if requested
    if filter_by == 'owing':
        students = [s for s in students if s.get('balance', 0) > 0]
    elif filter_by == 'paid':
        students = [s for s in students if s.get('balance', 0) <= 0]
    
    # Filter by department if requested
    if dept_filter:
        students = [s for s in students if s.get('department', '').lower() == dept_filter.lower()]
    
    # Sort by name - convert to string to handle None/NaN values
    students = sorted(students, key=lambda x: str(x.get('student_name', '')))
    
    # Calculate total outstanding and counts
    total_outstanding = sum(s.get('balance', 0) for s in students if s.get('balance', 0) > 0)
    outstanding_count = len([s for s in students if s.get('balance', 0) > 0])
    fully_paid_count = len([s for s in students if s.get('balance', 0) <= 0])
    
    return render_template('admin_finance_students.html', 
                           students=students, 
                           search_query=search_query,
                           edit_student=edit_student,
                           filter_by=filter_by,
                           dept_filter=dept_filter,
                           total_outstanding=total_outstanding,
                           outstanding_count=outstanding_count,
                           fully_paid_count=fully_paid_count)


@app.route('/admin/finance/student/<student_id>/payment', methods=['GET', 'POST'])
def admin_student_payment(student_id):
    """Process student payment."""
    if not check_finance_access():
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    student = SchoolStudent.get_by_student_id(student_id)
    if not student:
        flash('Student not found.', 'warning')
        return redirect(url_for('admin_finance_students'))
    
    # Get fee types for payment
    fee_types = FeeType.get_all()
    
    if request.method == 'POST':
        payment_amount = request.form.get('amount')
        fee_type = request.form.get('fee_type')
        payment_date = request.form.get('payment_date', datetime.now().strftime('%Y-%m-%d'))
        description = request.form.get('description', '')
        
        if not payment_amount or float(payment_amount) <= 0:
            flash('Please enter a valid payment amount.', 'warning')
        else:
            # Record the payment
            Payment.add(
                student_id=student_id,
                amount=float(payment_amount),
                payment_type=fee_type or 'General',
                payment_date=payment_date,
                description=description,
                receipt_number=f"REC-{student_id}-{int(datetime.now().timestamp())}"
            )
            flash(f'Payment of {payment_amount} recorded for {student.get("student_name")}.', 'success')
            return redirect(url_for('admin_student_history', student_id=student_id))
    
    return render_template('admin_student_payment.html', 
                           student=student,
                           fee_types=fee_types)


@app.route('/admin/finance/student/<student_id>/history')
def admin_student_history(student_id):
    """View student payment history."""
    if not check_finance_access():
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    student = SchoolStudent.get_by_student_id(student_id)
    if not student:
        flash('Student not found.', 'warning')
        return redirect(url_for('admin_finance_students'))
    
    # Get student payments using SchoolPayment
    payments = [p for p in SchoolPayment.all() 
                if str(p.get('student_id', '')).strip() == str(student_id).strip()]
    
    # Calculate totals
    total_paid = sum(p.get('amount', 0) for p in payments)
    
    return render_template('admin_student_history.html',
                           student=student,
                           payments=payments,
                           total_paid=total_paid)


@app.route('/admin/finance/students/delete/<int:student_id>', methods=['POST'])
def admin_finance_student_delete(student_id):
    """Delete a student record."""
    if not check_finance_access():
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    student = SchoolStudent.get_by_student_id(student_id)
    if student:
        SchoolStudent.delete(student.get('id'))
        student_name = student.get('student_name') or student.get('Student Name') or student_id
        flash(f'Student "{student_name}" has been deleted.', 'success')
    else:
        flash('Student not found.', 'warning')
    
    return redirect(url_for('admin_finance_students'))


@app.route('/admin/finance/student_results', methods=['GET', 'POST'])
def admin_finance_student_results():
    """View and edit student exam results"""
    if not check_finance_access():
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    search_query = request.args.get('search', '')
    
    # Get all students
    students = SchoolStudent.search(search_query)
    
    # Sort by student name - convert to string to handle None/NaN values
    students = sorted(students, key=lambda x: str(x.get('student_name', '')))
    
    return render_template('admin_finance_student_results.html', 
                           students=students, 
                           search_query=search_query)


@app.route('/admin/finance/student_results/edit/<student_id>', methods=['GET', 'POST'])
def admin_finance_student_results_edit(student_id):
    """Edit a student's exam results"""
    if not check_finance_access():
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Try to find student by student_id (string) or id (integer)
    student = SchoolStudent.get_by_student_id(student_id)
    if not student:
        # Try by numeric ID
        try:
            student = SchoolStudent.get_by_id(int(student_id))
        except:
            pass
    
    if not student:
        flash('Student not found.', 'warning')
        return redirect(url_for('admin_finance_student_results'))
    
    if request.method == 'POST':
        # Update student info
        student_name = request.form.get('student_name')
        department = request.form.get('department')
        parent_phone = request.form.get('parent_phone')
        
        # Math scores
        math_exams = request.form.get('math_exams_score', 0)
        math_class = request.form.get('math_class_score', 0)
        math_total = float(math_exams) + float(math_class) if math_exams and math_class else 0
        math_remarks = request.form.get('math_remarks', '')
        math_grade = request.form.get('math_grade', '')
        
        # Science scores
        science_exams = request.form.get('science_exams_score', 0)
        science_class = request.form.get('science_class_score', 0)
        science_total = float(science_exams) + float(science_class) if science_exams and science_class else 0
        science_remarks = request.form.get('science_remarks', '')
        science_grade = request.form.get('science_grade', '')
        
        # Social scores
        social_exams = request.form.get('social_exams_score', 0)
        social_class = request.form.get('social_class_score', 0)
        social_total = float(social_exams) + float(social_class) if social_exams and social_class else 0
        social_remarks = request.form.get('social_remarks', '')
        social_grade = request.form.get('social_grade', '')
        
        # Update the student record
        SchoolStudent.update(student_id, **{
            'student_name': student_name,
            'department': department,
            'parent_phone': parent_phone,
            'math_exams_score_2021_1': math_exams,
            'math_class_score_2021_1': math_class,
            'math_total_score_2021_1': math_total,
            'math_remarks_2021_1': math_remarks,
            'math_grade_2021_1': math_grade,
            'science_exams_score_2021_1': science_exams,
            'science_class_score_2021_1': science_class,
            'science_total_score_2021_1': science_total,
            'science_remarks_2021_1': science_remarks,
            'science_grade_2021_1': science_grade,
            'social_exams_score_2021_1': social_exams,
            'social_class_score_2021_1': social_class,
            'social_total_score_2021_1': social_total,
            'social_remarks_2021_1': social_remarks,
            'social_grade_2021_1': social_grade,
        })
        
        flash(f'Results for {student_name} updated successfully!', 'success')
        return redirect(url_for('admin_finance_student_results'))
    
    return render_template('admin_finance_student_results_edit.html', student=student)


@app.route('/admin/sync_students_push', methods=['GET', 'POST'])
def admin_sync_students_push():
    """Push student data from local Excel to Google Sheet"""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    sync_result = None
    
    if request.method == 'POST':
        sheet_url = request.form.get('students_sheet_url', '').strip()
        
        if not sheet_url:
            flash('Please provide a Google Sheet URL.', 'warning')
        else:
            try:
                # Read from local Excel
                excel_path = get_excel_path('students')
                df = pd.read_excel(excel_path)
                
                if df.empty:
                    flash('No student data found in local database.', 'warning')
                else:
                    # Convert to CSV for upload
                    # Note: For actual Google Sheets API upload, you would need proper authentication
                    # Here we save as CSV that can be imported to Google Sheets
                    csv_path = os.path.join(EXCEL_DB_DIR, 'students_export.csv')
                    df.to_csv(csv_path, index=False)
                    
                    sync_result = {
                        'status': 'success',
                        'records': len(df),
                        'file': csv_path
                    }
                    flash(f'Successfully exported {len(df)} students to CSV! You can now import this to Google Sheets.', 'success')
                    
            except Exception as e:
                flash(f'Error exporting students: {str(e)}', 'danger')
                print(f"Error exporting students: {e}")
    
    return render_template('admin_sync_students_push.html', sync_result=sync_result)


@app.route('/admin/students')
def admin_students():
    """Redirect /admin/students to /admin/finance/students"""
    return redirect(url_for('admin_finance_students'))


@app.route('/admin/results')
def admin_results():
    """Redirect /admin/results to /admin/finance/student_results"""
    return redirect(url_for('admin_finance_student_results'))


@app.route('/admin/download_exported_students')
def admin_download_exported_students():
    """Download the exported students CSV file"""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    csv_path = os.path.join(EXCEL_DB_DIR, 'students_export.csv')
    
    if os.path.exists(csv_path):
        return send_file(csv_path, as_attachment=True, download_name='students_export.csv')
    else:
        flash('No exported file found. Please export first.', 'warning')
        return redirect(url_for('admin_sync_students_push'))


@app.route('/admin/finance/expenses', methods=['GET', 'POST'])
def admin_finance_expenses():
    """Record and view expenses."""
    if not check_finance_access():
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    if request.method == 'POST':
        category = request.form.get('category')
        description = request.form.get('description')
        amount = float(request.form.get('amount', 0))
        vendor = request.form.get('vendor', '')
        approved_by = request.form.get('approved_by', '')
        notes = request.form.get('notes', '')
        
        if not category or not description or not amount:
            flash('Please fill in all required fields.', 'warning')
        else:
            # Record expense - use dictionary format for ExcelModel
            expense_data = {
                'category': category,
                'description': description,
                'amount': amount,
                'vendor': vendor,
                'approved_by': approved_by,
                'notes': notes
            }
            SchoolExpense.add(**expense_data)
            flash(f'Expense of GHS {amount} recorded successfully!', 'success')
            return redirect(url_for('admin_finance_expenses'))
    
    expenses = sorted(SchoolExpense.all(), key=lambda x: x.get('created_at', ''), reverse=True)[:100]
    total_expenses = sum(e.get('amount', 0) for e in SchoolExpense.all())
    
    return render_template('admin_finance_expenses.html', expenses=expenses, total_expenses=total_expenses)


@app.route('/admin/finance/collect', methods=['GET', 'POST'])
def admin_finance_collect():
    """Redirect to collect_payment route."""
    return redirect(url_for('admin_finance_collect_payment'))

@app.route('/admin/finance/collect_payment', methods=['GET', 'POST'])
def admin_finance_collect_payment():
    """Collect payment from student."""
    if not check_finance_access():
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Get fee categories for dropdown
    fee_categories = SchoolFeeType.all()
    
    # Generate next receipt number
    all_payments = SchoolPayment.all()
    last_receipt = sorted(all_payments, key=lambda x: x.get('id', 0), reverse=True)[0] if all_payments else None
    if last_receipt:
        last_num = int(last_receipt.get('receipt_number', 'REC-0').split('-')[-1])
        next_receipt_number = f'REC-{datetime.now().year}-{last_num + 1:03d}'
    else:
        next_receipt_number = f'REC-{datetime.now().year}-001'
    
    if request.method == 'POST':
        student_id = request.form.get('student_id_hidden')
        fee_category = request.form.get('fee_category')
        amount = float(request.form.get('payment_amount', 0))
        payment_method = request.form.get('payment_method')
        payment_date = request.form.get('payment_date')
        transaction_ref = request.form.get('transaction_ref', '')
        notes = request.form.get('payment_notes', '')
        
        if not student_id or not fee_category or not amount or not payment_method or not payment_date:
            flash('Please fill in all required fields.', 'warning')
        else:
            # VERIFY STUDENT EXISTS IN DATABASE FIRST
            student = SchoolStudent.get_by_student_id(student_id)
            if not student:
                flash(f'Student ID "{student_id}" not found in database. Please contact system admin to register the student first.', 'danger')
                return redirect(url_for('admin_finance_collect_payment'))
            
            # Get student name from hidden field
            student_name = request.form.get('student_name_hidden', '')
            
            # Find fee type by name (not ID)
            fee_type = SchoolFeeType.filter_by(name=fee_category)[0] if SchoolFeeType.filter_by(name=fee_category) else None
            if fee_type:
                # Create payment record as dictionary
                payment_data = {
                    'student_id': student_id,
                    'student_name': student_name,
                    'fee_type': fee_type.get('name', ''),
                    'amount': amount,
                    'payment_method': payment_method,
                    'payment_date': payment_date,
                    'transaction_ref': transaction_ref,
                    'receipt_number': next_receipt_number,
                    'notes': notes,
                    'status': 'completed'
                }
                # Add payment to local database and get the new ID
                payment_id = SchoolPayment.add(**payment_data)
                payment_data['id'] = payment_id
                
                # Sync payment to Google Sheet
                sync_result = sync_payment_to_google_sheet(payment_data)
                if sync_result:
                    flash(f'Payment of GHS {amount} recorded successfully for {student_name}! Receipt: {next_receipt_number} (Synced to Google Sheets)', 'success')
                else:
                    flash(f'Payment of GHS {amount} recorded for {student_name}! Receipt: {next_receipt_number} (Local only - Google Sheet sync pending)', 'warning')
                return redirect(url_for('admin_finance_collect_payment'))
    
    return render_template('admin_finance_collect_payment.html', 
                           fee_categories=fee_categories, 
                           next_receipt_number=next_receipt_number,
                           current_year=datetime.now().year)


@app.route('/admin/finance/view_payments')
def admin_finance_view_payments():
    """View all payment records."""
    if not check_finance_access():
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Get all payments with related data
    payments = sorted(SchoolPayment.all(), key=lambda x: (x.get('payment_date', ''), x.get('created_at', '')), reverse=True)[:500]
    total_collected = sum(p.get('amount', 0) for p in SchoolPayment.all())
    
    return render_template('admin_finance_view_payments.html', 
                           payments=payments,
                           current_date=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                           total_collected=total_collected)


@app.route('/admin/finance/student_account', methods=['GET', 'POST'])
def admin_finance_student_account():
    """View individual student account details and register new students."""
    if not check_finance_access():
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Get search query from URL parameter or form data
    search_query = request.args.get('search', '') or request.form.get('search_query', '')
    student_data = None
    search_results = []
    
    # Get fee categories for payment dropdown
    fee_categories = SchoolFeeType.all()
    
    # Get student payments if viewing a specific student
    student_payments = []
    selected_student = None
    
    # Handle form submission for recording payment
    if request.method == 'POST' and request.form.get('payment_student_id'):
        payment_student_id = request.form.get('payment_student_id', '').strip()
        fee_type = request.form.get('fee_type', '').strip()
        amount = float(request.form.get('payment_amount', 0))
        payment_method = request.form.get('payment_method', '')
        payment_date = request.form.get('payment_date', '')
        transaction_ref = request.form.get('transaction_ref', '')
        notes = request.form.get('payment_notes', '')
        
        if not payment_student_id or not fee_type or not amount or not payment_method or not payment_date:
            flash('Please fill in all required payment fields!', 'danger')
        else:
            # Generate receipt number
            all_payments = SchoolPayment.all()
            last_receipt = sorted(all_payments, key=lambda x: x.get('id', 0), reverse=True)[0] if all_payments else None
            if last_receipt:
                last_num = int(last_receipt.get('receipt_number', 'REC-0').split('-')[-1])
                next_receipt_number = f'REC-{datetime.now().year}-{last_num + 1:03d}'
            else:
                next_receipt_number = f'REC-{datetime.now().year}-001'
            
            # Get student name
            student = SchoolStudent.get_by_student_id(payment_student_id)
            student_name = student.get('student_name', '') if student else payment_student_id
            
            # Create payment record
            payment_data = {
                'student_id': payment_student_id,
                'student_name': student_name,
                'fee_type': fee_type,
                'amount': amount,
                'payment_method': payment_method,
                'payment_date': payment_date,
                'transaction_ref': transaction_ref,
                'receipt_number': next_receipt_number,
                'notes': notes,
                'status': 'completed'
            }
            SchoolPayment.add(**payment_data)
            
            # Sync payment to Google Sheet
            sync_payment_to_google_sheet(payment_data)
            
            flash(f'Payment of GH\u20b5 {amount} recorded successfully! Receipt: {next_receipt_number}', 'success')
            return redirect(url_for('admin_finance_student_account', search=payment_student_id))
    
    # Handle form submission for creating new student
    if request.method == 'POST' and request.form.get('student_id'):
        # Get form data
        student_id = request.form.get('student_id', '').strip()
        student_name = request.form.get('student_name', '').strip()
        department = request.form.get('department', '').strip()
        parent_phone = request.form.get('parent_phone', '').strip()
        student_class = request.form.get('student_class', '').strip()
        
        if not student_id or not student_name:
            flash('Student ID and Name are required!', 'danger')
            return render_template('admin_finance_student_account.html', 
                                   search_query=search_query,
                                   search_results=search_results,
                                   fee_categories=fee_categories)
        else:
            # Check if student already exists in local database
            existing = SchoolStudent.get_by_student_id(student_id)
            if existing:
                flash(f'Student with ID {student_id} already exists!', 'warning')
                return render_template('admin_finance_student_account.html', 
                                       search_query=search_query,
                                       search_results=search_results,
                                       fee_categories=fee_categories)
            else:
                # Add new student to local database
                SchoolStudent.add(
                    student_id=student_id,
                    student_name=student_name,
                    department=department,
                    parent_phone=parent_phone,
                    student_class=student_class
                )
                flash(f'Student {student_name} registered successfully!', 'success')
                # Redirect to show the new student
                return redirect(url_for('admin_finance_student_account', search=student_id))
    
    # Get all students from local database and Google Sheet
    all_students = []
    
    # First get all students from local Excel database
    local_students = SchoolStudent.all()
    for student in local_students:
        student['_source'] = 'local'
        # Calculate total paid from payments
        student_payments = [p for p in SchoolPayment.all() if str(p.get('student_id', '')) == str(student.get('student_id', student.get('id', '')))]
        student['total_paid'] = sum(p.get('amount', 0) for p in student_payments)
        student['payment_count'] = len(student_payments)
    all_students.extend(local_students)
    
    # Also get students from Google Sheet if available
    try:
        df = load_results_from_sheet()
        if not df.empty:
            df.columns = df.columns.str.strip().str.lower().str.replace(' ', '_')
            sheet_students = df.to_dict('records')
            # Add Google Sheet students that are not already in local database
            existing_ids = [s.get('student_id', s.get('id', '')) for s in local_students]
            for student in sheet_students:
                student_id = student.get('student_id', student.get('id', ''))
                if student_id and student_id not in existing_ids:
                    student['_source'] = 'google_sheet'
                    student['total_paid'] = 0
                    student['payment_count'] = 0
                    all_students.append(student)
    except Exception as e:
        print(f"Error loading Google Sheet students: {e}")
    
    # Sort students by name - convert to string to handle None/NaN values
    all_students = sorted(all_students, key=lambda x: str(x.get('student_name', '')))
    
    # Search for students if query provided
    if search_query:
        search_query_lower = search_query.lower()
        all_students = [s for s in all_students 
                       if search_query_lower in str(s.get('student_id', '')).lower() 
                       or search_query_lower in str(s.get('student_name', '')).lower()
                       or search_query_lower in str(s.get('department', '')).lower()]
    
    return render_template('admin_finance_student_account.html', 
                           search_query=search_query,
                           all_students=all_students,
                           fee_categories=fee_categories)


@app.route('/admin/finance/student_account/import/<student_id>', methods=['POST'])
def admin_finance_import_student(student_id):
    """Import a student from Google Sheet to local finance database."""
    if not check_finance_access():
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Get student from Google Sheet
    try:
        df = load_results_from_sheet()
        if not df.empty:
            # Normalize column names
            df.columns = df.columns.str.strip().str.lower().str.replace(' ', '_')
            
            # Find the student
            mask = df.apply(lambda row: str(row.get('student_id', '')).strip() == student_id.strip() or 
                           str(row.get('student_id', '')).strip() == student_id.strip(), axis=1)
            students = df[mask].to_dict('records')
            
            if students:
                student = students[0]
                
                # Check if already exists locally
                existing = SchoolStudent.get_by_student_id(student_id)
                if existing:
                    flash(f'Student {student_id} already exists in local database!', 'warning')
                else:
                    # Import to local database
                    SchoolStudent.add(
                        student_id=student.get('student_id', student_id),
                        student_name=student.get('student_name', ''),
                        department=student.get('department', ''),
                        parent_phone=student.get('parent_phone', ''),
                        student_class=student.get('class', '')
                    )
                    flash(f'Student {student.get("student_name", student_id)} imported to finance database!', 'success')
            else:
                flash(f'Student {student_id} not found in results database.', 'warning')
    except Exception as e:
        flash(f'Error importing student: {str(e)}', 'danger')
        print(f"Error importing student: {e}")
    
    return redirect(url_for('admin_finance_student_account', search=student_id))


@app.route('/admin/finance/reports')
def admin_finance_reports():
    """Financial reports and analytics."""
    if not check_finance_access():
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Calculate statistics
    total_revenue = sum(p.get('amount', 0) for p in SchoolPayment.all())
    total_expenses = sum(e.get('amount', 0) for e in SchoolExpense.all())
    pending_payments = SchoolPayment.count()
    
    # Get payments by month for chart data
    payments = SchoolPayment.all()
    monthly_data = {}
    for p in payments:
        month = str(p.get('payment_date', ''))[:7]  # Get YYYY-MM
        if month and month != 'nan':
            monthly_data[month] = monthly_data.get(month, 0) + p.get('amount', 0)
    monthly_payments = sorted([{'month': m, 'total': t} for m, t in monthly_data.items()], 
                              key=lambda x: x['month'], reverse=True)[:6]
    
    return render_template('admin_finance_reports.html',
                           total_revenue=total_revenue,
                           total_expenses=total_expenses,
                           pending_payments=pending_payments,
                           monthly_payments=monthly_payments)


# --- DATA SYNC ROUTES ---

@app.route('/admin/sync_excel', methods=['GET', 'POST'])
def admin_sync_excel():
    """Sync data from Google Sheets to local Excel database"""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    sync_results = []
    
    if request.method == 'POST':
        # Get the data types to sync
        data_types = request.form.getlist('data_types')
        
        # Google Sheet URLs (configure these for your sheets)
        sheet_urls = {
            'students': request.form.get('students_sheet_url', ''),
            'payments': request.form.get('payments_sheet_url', ''),
            'store_items': request.form.get('store_items_sheet_url', ''),
            'store_transactions': request.form.get('store_transactions_sheet_url', ''),
            'assets': request.form.get('assets_sheet_url', ''),
            'expenses': request.form.get('expenses_sheet_url', ''),
            'suppliers': request.form.get('suppliers_sheet_url', ''),
            'fee_types': request.form.get('fee_types_sheet_url', ''),
            'locations': request.form.get('locations_sheet_url', '')
        }
        
        for data_type in data_types:
            if data_type in sheet_urls and sheet_urls[data_type]:
                df = sync_from_google_sheet_to_excel(sheet_urls[data_type], data_type)
                if df is not None:
                    sync_results.append({
                        'type': data_type,
                        'status': 'success',
                        'records': len(df)
                    })
                else:
                    sync_results.append({
                        'type': data_type,
                        'status': 'failed',
                        'records': 0
                    })
            else:
                sync_results.append({
                    'type': data_type,
                    'status': 'skipped',
                    'reason': 'No URL provided'
                })
        
        flash('Data sync completed!', 'success')
    
    return render_template('admin_sync_excel.html', sync_results=sync_results)

from werkzeug.security import generate_password_hash, check_password_hash

# --- ADMIN: STAFF REGISTRATION ---
@app.route('/admin/register_staff', methods=['GET', 'POST'])
def register_staff():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        role = request.form.get('role')
        
        # Receive success status and message
        success, message = save_staff_user(username, password, role)
        
        if success:
            flash(message, 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            # This will show "Please close the Excel file" to the user
            flash(message, 'danger')
            return render_template('admin_register_staff.html')

    return render_template('admin_register_staff.html')
# --- DEPARTMENTAL LOGIN HANDLER ---
@app.route('/<dept>/login', methods=['GET', 'POST'])
def dept_login(dept):
    # Mapping URL names to roles
    role_map = {
        'finance': 'Finance Officer',
        'estate': 'Estate Manager',
        'store': 'Store Keeper',
        'hod': 'Head of Department'
    }
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        file_path = get_excel_path('staff_accounts')  # Use unified path from EXCEL_DB_DIR
        if os.path.exists(file_path):
            df = pd.read_excel(file_path)
            user_row = df[(df['username'] == username) & (df['role'] == role_map.get(dept))]
            
            if not user_row.empty:
                stored_hash = user_row.iloc[0]['password']
                if check_password_hash(stored_hash, password):
                    session[f'{dept}_logged_in'] = True
                    session['username'] = username
                    return redirect(url_for(f'{dept}_dashboard'))
        
        flash('Invalid username or password for this department.', 'danger')
    
    return render_template(f'{dept}_login.html', dept_title=role_map.get(dept, dept).upper())
# --- GENERIC LOGIN HANDLER ---
# def handle_department_login(role, template_name, session_key):
#     if request.method == 'POST':
#         username = request.form.get('username')
#         password = request.form.get('password')
        
#         # 1. Fetch user from Excel where username=username AND role=role
#         # 2. if user and check_password_hash(user['password'], password):
#         # 3.    session[session_key] = True
#         # 4.    return redirect(url_for(f'{role}_dashboard'))
        
#         flash('Invalid credentials for this department.', 'danger')
#     return render_template(template_name)

from werkzeug.security import check_password_hash

@app.route('/finance/login', methods=['GET', 'POST'])
def finance_login():
    if request.method == 'POST':
        username = request.form.get('username').strip()
        password = request.form.get('password').strip()
        file_path = get_excel_path('staff_accounts')  # Use unified path from EXCEL_DB_DIR

        if not os.path.exists(file_path):
            flash('System Error: Staff database not found. Please contact Admin.', 'danger')
            return render_template('admin_finance_login.html')

        try:
            # 1. Load the existing staff database
            df = pd.read_excel(file_path)
            
            # 2. Find the user by username and role
            user_row = df[(df['username'].str.strip() == username) & 
                         (df['role'].str.strip().str.lower() == 'finance')]

            if not user_row.empty:
                stored_hashed_pw = user_row.iloc[0]['password']

                # 3. Verify the hashed password
                if check_password_hash(stored_hashed_pw, password):
                    # 4. Login Success
                    session['finance_logged_in'] = True
                    session['staff_username'] = username
                    session['staff_role'] = 'finance'
                    flash(f'Welcome to the Finance Dashboard!', 'success')
                    return redirect(url_for('finance_dashboard'))
                else:
                    flash('Invalid password. Please try again.', 'danger')
            else:
                flash('User not found or not assigned to Finance department.', 'danger')

        except Exception as e:
            print(f"Login error: {e}")
            flash('An error occurred while processing your login.', 'danger')

    return render_template('admin_finance_login.html')

@app.route('/finance/dashboard')
def finance_dashboard():
    """Finance Officer Dashboard."""
    if not session.get('finance_logged_in'):
        flash('Please login to access the Finance dashboard.', 'warning')
        return redirect(url_for('finance_login'))
    
    # Get statistics
    total_students = SchoolStudent.count() if hasattr(SchoolStudent, 'count') else len(SchoolStudent.all())
    total_payments = SchoolPayment.count() if hasattr(SchoolPayment, 'count') else len(SchoolPayment.all())
    
    # Calculate total revenue from payments
    total_revenue = sum([p.get('amount', 0) for p in SchoolPayment.all()])
    
    # Calculate total expenses (assuming there's an expense tracking system)
    total_expenses = sum([e.get('amount', 0) for e in SchoolExpense.all()])
    
    # Calculate balance
    balance = total_revenue - total_expenses
    
    # Get debtors count and total outstanding
    try:
        debtors_count = len([a for a in SchoolStudentAccount.all() if a.get('balance', 0) > 0])
        total_outstanding = sum(a.get('balance', 0) for a in SchoolStudentAccount.all() if a.get('balance', 0) > 0)
    except:
        debtors_count = 0
        total_outstanding = 0
    
    # Get recent payments
    recent_payments = sorted(SchoolPayment.all(), key=lambda x: x.get('payment_date', ''), reverse=True)[:10]
    
    # Check if Google Sheets is configured
    google_sheet_url = globals().get('PAYMENTS_SHEET_URL', '')
    has_google_sheet = bool(google_sheet_url)
    
    return render_template('admin_finance.html',
                           total_students=total_students,
                           total_payments=total_payments,
                           total_revenue=total_revenue,
                           total_expenses=total_expenses,
                           balance=balance,
                           debtors_count=debtors_count,
                           total_outstanding=total_outstanding,
                           recent_payments=recent_payments,
                           has_google_sheet=has_google_sheet)

@app.route('/finance/logout')
def finance_logout():
    """Logout from finance dashboard."""
    session.pop('finance_logged_in', None)
    session.pop('staff_username', None)
    session.pop('staff_role', None)
    flash('You have been logged out from Finance dashboard.', 'info')
    return redirect(url_for('finance_login'))

@app.route('/estate/login', methods=['GET', 'POST'])
def estate_login():
    return handle_department_login('estate', 'estate_login.html', 'estate_logged_in')

@app.route('/store/login', methods=['GET', 'POST'])
def store_login():
    return handle_department_login('store', 'store_login.html', 'store_logged_in')

@app.route('/hod_staff/login', methods=['GET', 'POST'])
def hod_staff_login():
    return handle_department_login('hod_staff', 'hod_staff_login.html', 'hod_staff_logged_in')

@app.route('/admin/sync_students', methods=['GET', 'POST'])
def admin_sync_students():
    """Sync students from Google Sheets to local Excel database"""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    sync_result = None
    
    if request.method == 'POST':
        sheet_url = request.form.get('students_sheet_url', '').strip()
        
        if not sheet_url:
            flash('Please provide a Google Sheet URL.', 'warning')
        else:
            try:
                # Read from Google Sheet
                df = pd.read_csv(sheet_url)
                
                # Map Google Sheet columns to our database schema
                # Based on user's headers: Student ID, Student Name, Student Department, Parent Phone, and exam scores
                column_mapping = {
                    'student_id': ['Student ID', 'student_id', 'ID', 'id', 'STUDENT ID'],
                    'student_name': ['Student Name', 'student_name', 'Name', 'name', 'STUDENT NAME'],
                    'department': ['Student Department', 'department', 'Department', 'dept', 'STUDENT DEPARTMENT'],
                    'parent_phone': ['Parent Phone', 'parent_phone', 'Phone', 'phone', 'Mobile', 'PARENT PHONE'],
                    # Math scores - 2021 Semester 1
                    'math_exams_score_2021_1': ['Math Exams Score - 2021 Semester 1', 'Math Exams Score'],
                    'math_class_score_2021_1': ['Math Class Score - 2021 Semester 1', 'Math Class Score'],
                    'math_total_score_2021_1': ['Math Total Score - 2021 Semester 1', 'Math Total Score'],
                    'math_remarks_2021_1': ['Math Remarks - 2021 Semester 1', 'Math Remarks'],
                    'math_grade_2021_1': ['Math Grade - 2021 Semester 1', 'Math Grade'],
                    # Science scores - 2021 Semester 1
                    'science_exams_score_2021_1': ['Science Exams Score - 2021 Semester 1', 'Science Exams Score'],
                    'science_class_score_2021_1': ['Science Class Score - 2021 Semester 1', 'Science Class Score'],
                    'science_total_score_2021_1': ['Science Total Score - 2021 Semester 1', 'Science Total Score'],
                    'science_remarks_2021_1': ['Science Remarks - 2021 Semester 1', 'Science Remarks'],
                    'science_grade_2021_1': ['Science Grade - 2021 Semester 1', 'Science Grade'],
                    # Social scores - 2021 Semester 1
                    'social_exams_score_2021_1': ['Social Exams Score - 2021 Semester 1', 'Social Exams Score'],
                    'social_class_score_2021_1': ['Social Class Score - 2021 Semester 1', 'Social Class Score'],
                    'social_total_score_2021_1': ['Social Total Score - 2021 Semester 1', 'Social Total Score'],
                    'social_remarks_2021_1': ['Social Remarks - 2021 Semester 1', 'Social Remarks'],
                    'social_grade_2021_1': ['Social Grade - 2021 Semester 1', 'Social Grade'],
                }
                
                # Find matching columns
                mapped_df = pd.DataFrame()
                for our_col, possible_names in column_mapping.items():
                    for possible_name in possible_names:
                        if possible_name in df.columns:
                            mapped_df[our_col] = df[possible_name]
                            break
                
                if mapped_df.empty:
                    flash('Could not map columns from Google Sheet. Please check column headers.', 'danger')
                else:
                    # Generate IDs if not present
                    if 'student_id' not in mapped_df.columns or mapped_df['student_id'].isna().all():
                        mapped_df['student_id'] = [f'STU-{i+1:04d}' for i in range(len(mapped_df))]
                    
                    # Add created_at timestamp
                    mapped_df['created_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    
                    # Clear existing students and add new ones
                    excel_path = get_excel_path('students')
                    mapped_df.to_excel(excel_path, index=False)
                    
                    sync_result = {
                        'status': 'success',
                        'records': len(mapped_df)
                    }
                    flash(f'Successfully synced {len(mapped_df)} students from Google Sheet!', 'success')
                    
            except Exception as e:
                flash(f'Error syncing students: {str(e)}', 'danger')
                print(f"Error syncing students: {e}")
    
    return render_template('admin_sync_students.html', sync_result=sync_result)


@app.route('/admin/view_excel_data/<data_type>')
def admin_view_excel_data(data_type):
    """View data from local Excel database"""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    if data_type not in EXCEL_FILES:
        flash('Unknown data type', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    df = load_from_excel(data_type)
    
    if df is None or df.empty:
        flash(f'No data found in {data_type} Excel file', 'warning')
        return redirect(url_for('admin_dashboard'))
    
    # Convert DataFrame to list of dicts for template
    data = df.to_dict('records')
    
    return render_template('admin_view_excel_data.html', 
                           data=data, 
                           data_type=data_type,
                           columns=df.columns.tolist())


@app.route('/admin/download_excel/<data_type>')
def admin_download_excel(data_type):
    """Download Excel file"""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    if data_type not in EXCEL_FILES:
        flash('Unknown data type', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    excel_path = get_excel_path(data_type)
    
    if not os.path.exists(excel_path):
        flash(f'Excel file not found for {data_type}', 'warning')
        return redirect(url_for('admin_sync_excel'))
    
    return send_file(excel_path, 
                     as_attachment=True,
                     download_name=EXCEL_FILES[data_type])


# --- ESTATE MODULE ROUTES ---

@app.route('/admin/estate')
def admin_estate():
    """Estate management dashboard."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Get statistics
    total_assets = SchoolAsset.count()
    active_assets = len(SchoolAsset.filter_by(status='Active'))
    pending_maintenance = len([m for m in SchoolMaintenanceRequest.all() if m.get('status') in ['Reported', 'In Progress']])
    
    recent_assets = sorted(SchoolAsset.all(), key=lambda x: x.get('created_at', ''), reverse=True)[:10]
    
    return render_template('admin_estate.html',
                           total_assets=total_assets,
                           active_assets=active_assets,
                           pending_maintenance=pending_maintenance,
                           recent_assets=recent_assets)


@app.route('/admin/estate/locations', methods=['GET', 'POST'])
def admin_estate_locations():
    """Manage school locations."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description', '')
        
        if not name:
            flash('Please enter location name.', 'warning')
        else:
            existing = SchoolLocation.filter_by(name=name)[0] if SchoolLocation.filter_by(name=name) else None
            if existing:
                flash(f'Location "{name}" already exists.', 'warning')
            else:
                location_data = {'name': name, 'description': description}
                SchoolLocation.add(**location_data)
                flash(f'Location "{name}" added successfully!', 'success')
    
    locations = sorted(SchoolLocation.all(), key=lambda x: x.get('name', ''))
    return render_template('admin_estate_locations.html', locations=locations)


@app.route('/admin/estate/assets', methods=['GET', 'POST'])
def admin_estate_assets():
    """Manage school assets."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    if request.method == 'POST':
        name = request.form.get('name')
        asset_code = request.form.get('asset_code')
        category = request.form.get('category')
        location_id = request.form.get('location_id')
        purchase_value = float(request.form.get('purchase_value', 0))
        condition = request.form.get('condition', 'Good')
        notes = request.form.get('notes', '')
        
        if not name or not asset_code or not category:
            flash('Please fill in all required fields.', 'warning')
        else:
            existing = SchoolAsset.filter_by(asset_code=asset_code)[0] if SchoolAsset.filter_by(asset_code=asset_code) else None
            if existing:
                flash(f'Asset code "{asset_code}" already exists.', 'warning')
            else:
                asset_data = {
                    'name': name,
                    'asset_code': asset_code,
                    'category': category,
                    'location_id': location_id,
                    'purchase_value': purchase_value,
                    'current_value': purchase_value,
                    'condition': condition,
                    'notes': notes
                }
                SchoolAsset.add(**asset_data)
                flash(f'Asset "{name}" registered successfully!', 'success')
    
    search = request.args.get('search', '')
    category_filter = request.args.get('category', '')
    
    assets = SchoolAsset.all()
    
    if search:
        assets = [a for a in assets if search.lower() in a.get('name', '').lower() or search.lower() in a.get('asset_code', '').lower()]
    
    if category_filter:
        assets = [a for a in assets if a.get('category') == category_filter]
    
    assets = sorted(assets, key=lambda x: x.get('name', ''))
    locations = SchoolLocation.all()
    
    return render_template('admin_estate_assets.html', assets=assets, locations=locations, 
                           search=search, category_filter=category_filter)


@app.route('/admin/estate/move/<int:asset_id>', methods=['GET', 'POST'])
def admin_estate_move_asset(asset_id):
    """Move asset to new location."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    asset = SchoolAsset.get_by_id(asset_id)
    if not asset:
        abort(404)
    
    if request.method == 'POST':
        new_location_id = request.form.get('location_id')
        reason = request.form.get('reason', '')
        
        if not new_location_id:
            flash('Please select a new location.', 'warning')
        else:
            from_location_id = asset.get('location_id')
            
            # Update asset location
            SchoolAsset.update(asset_id, location_id=new_location_id, updated_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            
            # Record movement
            movement_data = {
                'asset_id': asset_id,
                'from_location_id': from_location_id,
                'to_location_id': new_location_id,
                'moved_by': session.get('admin_username', 'Admin'),
                'reason': reason
            }
            SchoolAssetMovement.add(**movement_data)
            
            flash(f'Asset moved successfully!', 'success')
            return redirect(url_for('admin_estate_assets'))
    
    locations = SchoolLocation.all()
    return render_template('admin_estate_move.html', asset=asset, locations=locations)


@app.route('/admin/estate/maintenance', methods=['GET', 'POST'])
def admin_estate_maintenance():
    """Report and track maintenance requests."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    if request.method == 'POST':
        asset_id = request.form.get('asset_id')
        location_id = request.form.get('location_id')
        issue_description = request.form.get('issue_description')
        priority = request.form.get('priority', 'Medium')
        estimated_cost = float(request.form.get('estimated_cost', 0))
        notes = request.form.get('notes', '')
        
        if not issue_description:
            flash('Please describe the issue.', 'warning')
        else:
            maintenance_data = {
            'asset_id': asset_id if asset_id else None,
            'location_id': location_id if location_id else None,
            'issue_description': issue_description,
            'priority': priority,
            'estimated_cost': estimated_cost,
            'reported_by': session.get('admin_username', 'Admin'),
            'notes': notes,
            'status': 'Reported'
            }
            SchoolMaintenanceRequest.add(**maintenance_data)
            
            flash('Maintenance request logged successfully!', 'success')
            return redirect(url_for('admin_estate_maintenance'))
    
    status_filter = request.args.get('status', 'all')
    requests_list = SchoolMaintenanceRequest.all()
    
    if status_filter != 'all':
        requests_list = [r for r in requests_list if r.get('status') == status_filter]
    
    # Sort by priority and created_at
    priority_order = {'High': 0, 'Medium': 1, 'Low': 2}
    requests_list = sorted(requests_list, key=lambda x: (priority_order.get(x.get('priority'), 1), x.get('created_at', '')), reverse=True)
    
    assets = SchoolAsset.all()
    locations = SchoolLocation.all()
    
    return render_template('admin_estate_maintenance.html', 
                           requests=requests_list, 
                           assets=assets, 
                           locations=locations,
                           status_filter=status_filter)


@app.route('/admin/estate/maintenance/<int:request_id>/complete', methods=['POST'])
def admin_estate_complete_maintenance(request_id):
    """Mark maintenance request as completed."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    maintenance = SchoolMaintenanceRequest.get_by_id(request_id)
    if not maintenance:
        abort(404)
    
    actual_cost = float(request.form.get('actual_cost', 0))
    contractor = request.form.get('contractor', '')
    notes = request.form.get('notes', '')
    
    SchoolMaintenanceRequest.update(request_id, 
        status='Completed',
        actual_cost=actual_cost,
        contractor=contractor,
        completed_date=datetime.now().strftime('%Y-%m-%d'),
        notes=notes
    )
        
    
    flash('Maintenance request marked as completed!', 'success')
    
    return redirect(url_for('admin_estate_maintenance'))

# =============================================================================
# PAYSTACK CONFIGURATION FOR STUDENT PAYMENT GATEWAY
# =============================================================================
# Get your keys from https://dashboard.paystack.com/#/settings/api
PAYSTACK_SECRET_KEY = 'sk_test_your_secret_key_here'  # Replace with your Paystack test/live secret key
PAYSTACK_PUBLIC_KEY = 'pk_test_your_public_key_here'  # Replace with your Paystack test/live public key
PAYSTACK_BASE_URL = 'https://api.paystack.co'

# Result viewing fee configuration (in Ghana Cedis)
RESULT_VIEWING_FEE = 10.00  # Amount students must pay to view results
RESULT_FEE_DESCRIPTION = 'Examination Result Access Fee'

# =============================================================================
# STUDENT PAYMENT GATEWAY ROUTES (Paystack Mobile Money)
# Students must pay before viewing results
# =============================================================================

@app.route('/student/pay_result_access', methods=['GET', 'POST'])
def student_pay_result_access():
    """Payment page for students to access their results."""
    # Get stored student info from session
    pending_student = session.get('pending_student')
    
    if not pending_student:
        flash('Please search for your results first.', 'warning')
        return redirect(url_for('student_login'))
    
    student_name = pending_student.get('student_name')
    student_phone = pending_student.get('parent_phone')
    student_id = pending_student.get('student_id')
    
    # Check if student already paid for result viewing
    existing_payment = None
    payments = SchoolPayment.all()
    for p in payments:
        if p.get('fee_type', '').lower() == 'result viewing' and \
           str(p.get('student_id', '')) == str(student_id):
            existing_payment = p
            break
    
    if existing_payment and existing_payment.get('status') == 'completed':
        # Already paid, redirect to results
        session['result_payment_verified'] = True
        session['result_payment_receipt'] = existing_payment.get('receipt_number', '')
        flash('Payment already verified! You can view your results.', 'success')
        return redirect(url_for('student_login'))
    
    if request.method == 'POST':
        # Process payment
        payment_method = request.form.get('payment_method', 'mobile_money')
        mobile_network = request.form.get('mobile_network', 'mtn')
        phone_number = request.form.get('phone_number', '')
        
        # Clean phone number
        phone_clean = phone_number.replace(' ', '').replace('-', '')
        if phone_clean.startswith('0'):
            phone_clean = '233' + phone_clean[1:]
        elif not phone_clean.startswith('233'):
            phone_clean = '233' + phone_clean
        
        # Generate receipt number
        all_payments = SchoolPayment.all()
        last_payment = sorted(all_payments, key=lambda x: x.get('id', 0), reverse=True)[0] if all_payments else None
        if last_payment:
            last_num = int(last_payment.get('receipt_number', 'REC-0').split('-')[-1])
            receipt_number = f'REC-{datetime.now().year}-{last_num + 1:03d}'
        else:
            receipt_number = f'REC-{datetime.now().year}-001'
        
        # Create pending payment record
        payment_data = {
            'student_id': student_id,
            'student_name': student_name,
            'fee_type': 'Result Viewing',
            'amount': RESULT_VIEWING_FEE,
            'payment_method': f'{payment_method}_{mobile_network}',
            'payment_date': datetime.now().strftime('%Y-%m-%d'),
            'receipt_number': receipt_number,
            'status': 'pending',
            'notes': f'Payment initiated via Paystack {mobile_network}',
            'transaction_ref': f'TXN-{student_id}-{int(datetime.now().timestamp())}',
            'phone_number': phone_clean
        }
        SchoolPayment.add(**payment_data)
            
            # Sync payment to Google Sheet
        sync_payment_to_google_sheet(payment_data)
        
        # Store payment info in session
        session['pending_result_payment'] = {
            'student_id': student_id,
            'student_name': student_name,
            'amount': RESULT_VIEWING_FEE,
            'mobile_network': mobile_network,
            'phone_number': phone_clean,
            'receipt_number': receipt_number
        }
        
        flash('Please complete the payment on your mobile device.', 'info')
        return redirect(url_for('student_verify_result_payment'))
    
    return render_template('student_pay_result.html',
                           student_name=student_name,
                           student_phone=student_phone,
                           amount=RESULT_VIEWING_FEE,
                           public_key=PAYSTACK_PUBLIC_KEY)


@app.route('/student/verify_result_payment', methods=['GET', 'POST'])
def student_verify_result_payment():
    """Verify student result payment completion."""
    pending_payment = session.get('pending_result_payment')
    
    if not pending_payment:
        flash('No pending payment found.', 'warning')
        return redirect(url_for('student_login'))
    
    if request.method == 'POST':
        # User confirmed payment on mobile
        student_id = pending_payment.get('student_id')
        
        # Update payment status to completed
        payments = SchoolPayment.all()
        for p in payments:
            if str(p.get('student_id', '')) == str(student_id) and p.get('status') == 'pending':
                SchoolPayment.update(p['id'], status='completed')
                # Sync updated payment to Google Sheet
                sync_payment_to_google_sheet(p)
                session['result_payment_verified'] = True
                session['result_payment_receipt'] = p.get('receipt_number', '')
                break
        
        # Clear pending payment
        session.pop('pending_result_payment', None)
        
        flash('Payment verified successfully! You can now view your results.', 'success')
        return redirect(url_for('student_login'))
    
    return render_template('student_verify_payment.html',
                           payment=pending_payment)


@app.route('/api/paystack/webhook', methods=['POST'])
def paystack_webhook():
    """Handle Paystack webhook callbacks for payment verification."""
    # In production, verify Paystack signature here
    # For demo purposes, we just acknowledge receipt
    return jsonify({'status': 'success'})


@app.route('/api/paystack/verify_payment', methods=['POST'])
def paystack_verify_payment():
    """Verify a Paystack payment and create payment record."""
    try:
        data = request.get_json()
        reference = data.get('reference', '')
        phone = data.get('phone', '')
        network = data.get('network', 'mtn')
        
        if not reference:
            return jsonify({'success': False, 'message': 'Payment reference required'}), 400
        
        # Get pending student info from session
        pending_student = session.get('pending_student')
        if not pending_student:
            return jsonify({'success': False, 'message': 'Session expired. Please search for results again.'}), 400
        
        student_id = pending_student.get('student_id')
        student_name = pending_student.get('student_name')
        
        # Verify payment with Paystack API
        verify_url = f'{PAYSTACK_BASE_URL}/transaction/verify/{reference}'
        headers = {
            'Authorization': f'Bearer {PAYSTACK_SECRET_KEY}',
            'Content-Type': 'application/json'
        }
        
        response = requests.get(verify_url, headers=headers)
        result = response.json()
        
        if result.get('status') and result.get('data', {}).get('status') == 'success':
            # Payment verified successfully
            # Generate receipt number
            all_payments = SchoolPayment.all()
            last_payment = sorted(all_payments, key=lambda x: x.get('id', 0), reverse=True)[0] if all_payments else None
            if last_payment:
                try:
                    last_num = int(last_payment.get('receipt_number', 'REC-0').split('-')[-1])
                    receipt_number = f'REC-{datetime.now().year}-{last_num + 1:03d}'
                except:
                    receipt_number = f'REC-{datetime.now().year}-001'
            else:
                receipt_number = f'REC-{datetime.now().year}-001'
            
            # Create payment record
            payment_data = {
                'student_id': str(student_id),
                'student_name': student_name,
                'fee_type': 'Result Viewing',
                'amount': RESULT_VIEWING_FEE,
                'payment_method': f'mobile_money_{network}',
                'payment_date': datetime.now().strftime('%Y-%m-%d'),
                'receipt_number': receipt_number,
                'status': 'completed',
                'notes': f'Payment via Paystack. Ref: {reference}',
                'transaction_ref': reference,
                'phone_number': phone
            }
            SchoolPayment.add(**payment_data)
            
            # Sync payment to Google Sheet
            sync_payment_to_google_sheet(payment_data)
            
            # Set session variables for result access
            session['result_payment_verified'] = True
            session['result_payment_receipt'] = receipt_number
            
            return jsonify({
                'success': True,
                'message': 'Payment verified successfully',
                'receipt_number': receipt_number
            })
        else:
            return jsonify({
                'success': False,
                'message': result.get('message', 'Payment verification failed')
            }), 400
            
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# Excel database is initialized at startup - no additional setup needed
print("All systems ready! Excel database is configured.")
@app.route('/api/database/auto_create_worksheets', methods=['POST'])
def api_database_auto_create_worksheets():
    """API endpoint to automatically create ALL required worksheets in Google Sheet"""
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    try:
        gc = get_google_sheet_client()
        if not gc:
            return jsonify({'success': False, 'message': 'Failed to connect to Google Sheets'})
        
        spreadsheet = gc.open_by_key(UNIFIED_GOOGLE_SHEET_ID)
        
        results = auto_create_all_worksheets(gc, spreadsheet)
        
        successful = len([r for r in results.values() if r.get('success')])
        
        return jsonify({
            'success': True,
            'message': f'Successfully created/verified {successful}/{len(results)} worksheets!',
            'results': results
        })
    
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500


# =============================================================================
# ENTER RESULTS ROUTE - Form to enter student results matching Google Sheet headers
# =============================================================================

@app.route('/admin/enter_results', methods=['GET', 'POST'])
def admin_enter_results():
    """Form to enter student exam results directly to Google Sheet"""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    student = None
    academic_year = request.args.get('academic_year', '')
    semester = request.args.get('semester', '')
    student_id = request.args.get('student_id', '')
    
    # Core subjects (same for all students)
    core_subjects = ['Math', 'Science', 'Social', 'English', 'ICT', 'Entrepreneur']
    
    # Department-specific subjects based on your sheet headers
    department_subjects_map = {
        'electricals': ['Principles of electrical', 'Electrical installation'],
        'welding': ['Welding Principles', 'Welding Fabrication'],
        'plumbing': ['Plumbing Principles', 'Plumbing Technology'],
        'building': ['Construction Practice', 'Construction Materials'],
        'fashion': ['Garment design', 'Garment Construction'],
        'Catering': ['Catering Principles', 'Catering Technology'],
        'wood': ['Wood Principles', 'Wood Technology'],
        'General Science': ['Physics', 'Chemistry', 'Biology'],
        'General Arts': ['History', 'Literature', 'Government'],
        'Business': ['Accounting', 'Economics', 'Business Management'],
    }
    
    department_subjects = []
    
    if request.method == 'POST':
        # Process form submission
        student_id = request.form.get('student_id', '')
        academic_year = request.form.get('academic_year', '')
        semester = request.form.get('semester', '')
        
        if not student_id or not academic_year or not semester:
            flash('Please fill in all required fields.', 'warning')
            return redirect(url_for('admin_enter_results') + f'?student_id={student_id}&academic_year={academic_year}&semester={semester}')
        
        # Load current data
        df = load_results_from_sheet()
        
        if df.empty:
            flash('No student data found. Please upload student data first.', 'danger')
            return redirect(url_for('admin_dashboard'))
        
        # Find the student
        student_rows = df[df['Student ID'].astype(str) == str(student_id)]
        
        if student_rows.empty:
            flash(f'Student ID "{student_id}" not found!', 'danger')
            return redirect(url_for('admin_enter_results'))
        
        idx = student_rows.index[0]
        
        # Create semester suffix like " - 2026 Semester 1"
        semester_suffix = f" - {academic_year} {semester}"
        print(f"DEBUG: Saving results for {academic_year} {semester}")
        print(f"DEBUG: Semester suffix will be: {semester_suffix}")
        
        # Update all score fields with semester suffix in column names
        for key, value in request.form.items():
            if key.startswith('score_'):
                # Extract column name from key
                # The key format is: score_SubjectName_ScoreType (e.g., score_Math_Exams Score)
                # NOTE: Template uses underscores for HTML form compatibility, but we need spaces in the sheet
                original_col = key[6:]  # Remove 'score_' prefix
                
                # CRITICAL FIX: Replace underscores with spaces to match 2025 format
                # e.g., "Math_Exams Score" -> "Math Exams Score"
                # This ensures consistency with existing 2025 data
                original_col = original_col.replace('_', ' ')
                
                # Create column name WITH semester suffix
                col_name_with_suffix = f"{original_col}{semester_suffix}"
                
                print(f"DEBUG: Processing {key} -> {col_name_with_suffix} = {value}")
                
                if col_name_with_suffix not in df.columns:
                    # Add new column if it doesn't exist
                    print(f"DEBUG: Adding new column: {col_name_with_suffix}")
                    df[col_name_with_suffix] = ''
                
                df.loc[idx, col_name_with_suffix] = value
        
        # Save to Google Sheet
        success = save_results_to_sheet_fix(df)
        
        if success:
            flash(f'Results for {student_id} ({academic_year} {semester}) saved successfully to Google Sheet!', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Failed to save results. Please try again.', 'danger')
            student = student_rows.iloc[0].to_dict()
            department = student.get('Student Department', '')
            department_subjects = department_subjects_map.get(department, [])
    
    elif student_id:
        # Load student data for display
        df = load_results_from_sheet()
        
        if not df.empty:
            student_rows = df[df['Student ID'].astype(str) == str(student_id)]
            if not student_rows.empty:
                student = student_rows.iloc[0].to_dict()
                department = student.get('Student Department', '')
                department_subjects = department_subjects_map.get(department, [])
    
    # Dynamically generate years from 2020 to 2050
    years = [str(year) for year in range(2020, 2051)]
    semesters = ['Semester 1', 'Semester 2']
    
    return render_template('admin_enter_results.html',
                           student=student,
                           academic_year=academic_year,
                           semester=semester,
                           student_id=student_id,
                           core_subjects=core_subjects,
                           department_subjects=department_subjects,
                           years=years,
                           semesters=semesters)


def save_student_results_to_sheet(df):
    """Save DataFrame back to Google Sheet using gspread API"""
    try:
        if not os.path.exists(SERVICE_ACCOUNT_FILE):
            print(f"Service account file not found: {SERVICE_ACCOUNT_FILE}")
            return False
        
        credentials = ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_FILE, SCOPES)
        gc = gspread.authorize(credentials)
        
        # Use the UNIFIED Google Sheet
        sh = gc.open_by_key(UNIFIED_GOOGLE_SHEET_ID)
        
        try:
            worksheet = sh.worksheet('Students')
        except gspread.exceptions.WorksheetNotFound:
            # Create new worksheet
            sh.add_worksheet('Students', rows=len(df) + 10, cols=len(df.columns) + 50)
            worksheet = sh.worksheet('Students')
        
        # Clear existing data
        worksheet.clear()
        
        # Write headers
        headers = df.columns.tolist()
        worksheet.append_row(headers)
        
        # Write data rows
        for _, row in df.iterrows():
            row_data = []
            for val in row:
                if pd.isna(val):
                    row_data.append('')
                else:
                    row_data.append(str(val))
            worksheet.append_row(row_data)
        
        print(f"Successfully saved {len(df)} rows to Google Sheet Students worksheet")
        return True
        
    except Exception as e:
        print(f"Error saving to Google Sheet: {e}")
        return False
# ====== NEW FUNCTION: Complete Google Sheet Save ======
def save_results_to_sheet_fix(df):
    """
    Save DataFrame to Google Sheet Students worksheet.
    Fixes the issue where edit results were not saved.
    """
    try:
        print("="*60)
        print("SAVE_RESULTS_TO_SHEET_FIX: Starting save...")
        
        # Get Google Sheets client
        gc = get_google_sheet_client()
        if not gc:
            print("ERROR: Failed to get Google Sheets client")
            print("Check that SERVICE_ACCOUNT_FILE exists and is valid")
            print("="*60)
            return False
        
        print(f"Using UNIFIED_GOOGLE_SHEET_ID: {UNIFIED_GOOGLE_SHEET_ID}")
        
        # Open the spreadsheet
        sh = gc.open_by_key(UNIFIED_GOOGLE_SHEET_ID)
        
        # Get or create 'Students' worksheet
        try:
            worksheet = sh.worksheet('Students')
            print("Found 'Students' worksheet - will update it")
        except gspread.exceptions.WorksheetNotFound:
            print("'Students' worksheet not found - creating new one...")
            worksheet = sh.add_worksheet(title='Students', rows=1000, cols=50)
            print("Created new 'Students' worksheet")
        
        # Prepare all values - headers first, then data
        all_values = [df.columns.tolist()] + df.values.tolist()
        
        # Convert all values to strings, handle NaN
        clean_values = []
        for row in all_values:
            clean_row = []
            for val in row:
                if pd.isna(val):
                    clean_row.append('')
                elif isinstance(val, (int, float)):
                    clean_row.append(str(val))
                else:
                    clean_row.append(str(val))
            clean_values.append(clean_row)
        
        num_rows = len(clean_values)
        num_cols = len(df.columns)
        
        print(f"Data size: {num_rows} rows x {num_cols} columns")
        
        # Calculate the update range properly
        # Handle columns beyond Z (like AA, AB, etc.)
        def col_to_letter(n):
            """Convert column number (1-based) to Excel letter format"""
            result = ""
            while n > 0:
                n, remainder = divmod(n - 1, 26)
                result = chr(65 + remainder) + result
            return result
        
        end_col = col_to_letter(num_cols)
        
        range_name = f"A1:{end_col}{num_rows}"
        print(f"Update range: {range_name}")
        
        # Clear old data and write new data
        worksheet.clear()
        worksheet.update(values=clean_values, range_name=range_name)
        
        print(f"SUCCESS: Saved {num_cols} columns and {num_rows} rows to Google Sheet")
        print("="*60)
        return True
        
    except ImportError as e:
        print(f"IMPORT ERROR: {e}")
        print("Make sure gspread is installed: pip install gspread")
        print("="*60)
        return False
    except Exception as e:
        print(f"ERROR saving to Google Sheet: {e}")
        import traceback
        traceback.print_exc()
        print("="*60)
        return False
# ====== END OF NEW FUNCTION ======


def save_instructors_to_sheet():
    """
    Save instructors data to Google Sheet 'Instructors' worksheet.
    This function reads from the local Excel file and syncs to Google Sheets.
    """
    try:
        print("="*60)
        print("SAVE_INSTRUCTORS_TO_SHEET: Starting save...")
        
        # Get Google Sheets client
        gc = get_google_sheet_client()
        if not gc:
            print("ERROR: Failed to get Google Sheets client")
            return False
        
        # Load instructors from local Excel
        df = pd.read_excel(get_excel_path('instructors'))
        
        if df.empty:
            print("No instructors data to save")
            return True
        
        print(f"Loaded {len(df)} instructors from local Excel")
        
        # Open the spreadsheet
        sh = gc.open_by_key(UNIFIED_GOOGLE_SHEET_ID)
        
        # Get or create 'Instructors' worksheet
        try:
            worksheet = sh.worksheet('Instructors')
            print("Found 'Instructors' worksheet - will update it")
        except gspread.exceptions.WorksheetNotFound:
            print("'Instructors' worksheet not found - creating new one...")
            worksheet = sh.add_worksheet(title='Instructors', rows=1000, cols=10)
            print("Created new 'Instructors' worksheet")
        
        # Prepare all values - headers first, then data
        all_values = [df.columns.tolist()] + df.values.tolist()
        
        # Convert all values to strings, handle NaN
        clean_values = []
        for row in all_values:
            clean_row = []
            for val in row:
                if pd.isna(val):
                    clean_row.append('')
                elif isinstance(val, (int, float)):
                    clean_row.append(str(val))
                else:
                    clean_row.append(str(val))
            clean_values.append(clean_row)
        
        num_rows = len(clean_values)
        num_cols = len(df.columns)
        
        print(f"Data size: {num_rows} rows x {num_cols} columns")
        
        # Calculate the update range properly
        def col_to_letter(n):
            """Convert column number (1-based) to Excel letter format"""
            result = ""
            while n > 0:
                n, remainder = divmod(n - 1, 26)
                result = chr(65 + remainder) + result
            return result
        
        end_col = col_to_letter(num_cols)
        
        range_name = f"A1:{end_col}{num_rows}"
        print(f"Update range: {range_name}")
        
        # Clear old data and write new data
        worksheet.clear()
        worksheet.update(values=clean_values, range_name=range_name)
        
        print(f"SUCCESS: Saved {num_cols} columns and {num_rows} rows to Google Sheet Instructors worksheet")
        print("="*60)
        return True
        
    except ImportError as e:
        print(f"IMPORT ERROR: {e}")
        print("Make sure gspread is installed: pip install gspread")
        print("="*60)
        return False
    except Exception as e:
        print(f"ERROR saving instructors to Google Sheet: {e}")
        import traceback
        traceback.print_exc()
        print("="*60)
        return False


# ============================================
# ADMIN MANAGE FORMS ROUTE
# ============================================
@app.route('/admin/manage_forms', methods=['GET', 'POST'])
def admin_manage_forms():
    """Manage academic forms (e.g., Form 1, Form 2, Level 1, etc.)"""
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    
    # Create forms file if not exists
    init_excel_db()
    
    if request.method == 'POST':
        form_name = request.form.get('form_name')
        form_level = request.form.get('form_level', '1')
        
        if form_name:
            df_forms = load_excel_data('forms')
            
            # Get next FormID
            if df_forms.empty:
                next_id = 1
            else:
                next_id = int(df_forms['FormID'].max()) + 1
            
            # Add new form
            new_form = pd.DataFrame([{
                'FormID': next_id,
                'FormName': form_name,
                'Level': form_level,
                'created_at': datetime.now().strftime('%Y-%m-%d %H:%M')
            }])
            df_forms = pd.concat([df_forms, new_form], ignore_index=True)
            save_excel_data('forms', df_forms)
            
            # Sync to Google Sheets
            try:
                save_forms_to_sheet()
            except Exception as e:
                print(f"Error syncing forms to Google Sheet: {e}")
            
            flash(f'Form "{form_name}" added successfully!', 'success')
        else:
            flash('Form name is required.', 'danger')
    
    df_forms = load_excel_data('forms')
    df_students = load_excel_data('students')
    
    # Count students per form - handle NaN values
    forms_with_counts = []
    if not df_forms.empty:
        for _, form in df_forms.iterrows():
            # Convert to dict and replace NaN with None for proper Jinja2 handling
            form_dict = {}
            for key, value in form.to_dict().items():
                import math
                if isinstance(value, float) and math.isnan(value):
                    form_dict[key] = None
                else:
                    form_dict[key] = value
            
            form_name_val = form_dict.get('FormName', form_dict.get('FormID', ''))
            form_name_str = str(form_name_val) if form_name_val is not None else ''
            
            # Count students in this form
            try:
                count = len(df_students[df_students['Form'].astype(str) == form_name_str])
            except:
                count = 0
            
            form_dict['student_count'] = count
            forms_with_counts.append(form_dict)
    
    return render_template('admin_manage_forms.html', forms=forms_with_counts)




@app.route('/admin/edit_form/<form_id>', methods=['GET', 'POST'])
def admin_edit_form(form_id):
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    
    df_forms = load_excel_data('forms')
    form_data = df_forms[df_forms['FormID'].astype(str) == str(form_id)]
    
    if form_data.empty:
        flash('Form not found.', 'danger')
        return redirect(url_for('admin_manage_forms'))
    
    if request.method == 'POST':
        form_name = request.form.get('form_name')
        form_level = request.form.get('form_level', '1')
        
        if form_name:
            df_forms.loc[df_forms['FormID'].astype(str) == str(form_id), 'FormName'] = form_name
            df_forms.loc[df_forms['FormID'].astype(str) == str(form_id), 'Level'] = form_level
            save_excel_data('forms', df_forms)
            flash('Form updated successfully!', 'success')
            return redirect(url_for('admin_manage_forms'))
    
    return render_template('admin_manage_forms.html', edit_form=form_data.iloc[0].to_dict())




@app.route('/admin/delete_form/<form_id>')
def admin_delete_form(form_id):
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    
    try:
        df_forms = load_excel_data('forms')
        
        if df_forms.empty:
            flash('No forms found to delete.', 'warning')
            return redirect(url_for('admin_manage_forms'))
        
        # Filter out the form to delete
        df_forms = df_forms[df_forms['FormID'].astype(str) != str(form_id)]
        save_excel_data('forms', df_forms)
        
        # Sync to Google Sheets
        try:
            save_forms_to_sheet()
        except Exception as e:
            print(f"Error syncing forms to Google Sheet: {e}")
        
        flash('Form deleted successfully!', 'success')
    except Exception as e:
        flash(f'Error deleting form: {str(e)}', 'danger')
    
    return redirect(url_for('admin_manage_forms'))


def save_forms_to_sheet():
    """
    Save forms data to Google Sheet 'Forms' worksheet.
    This function reads from the local Excel file and syncs to Google Sheets.
    """
    try:
        gc = get_google_sheet_client()
        if not gc:
            print("ERROR: Failed to get Google Sheets client for forms")
            return False
        
        # Load forms from local Excel
        df = load_excel_data('forms')
        
        if df.empty:
            print("No forms data to save")
            return True
        
        print(f"Loaded {len(df)} forms from local Excel")
        
        # Open the spreadsheet
        sh = gc.open_by_key(UNIFIED_GOOGLE_SHEET_ID)
        
        # Get or create 'Forms' worksheet
        try:
            worksheet = sh.worksheet('Forms')
            print("Found 'Forms' worksheet - will update it")
        except gspread.exceptions.WorksheetNotFound:
            print("'Forms' worksheet not found - creating new one...")
            worksheet = sh.add_worksheet(title='Forms', rows=1000, cols=10)
            print("Created new 'Forms' worksheet")
        
        # Prepare all values - headers first, then data
        all_values = [df.columns.tolist()] + df.values.tolist()
        
        # Convert all values to strings, handle NaN
        clean_values = []
        for row in all_values:
            clean_row = []
            for val in row:
                if pd.isna(val):
                    clean_row.append('')
                elif isinstance(val, (int, float)):
                    clean_row.append(str(val))
                else:
                    clean_row.append(str(val))
            clean_values.append(clean_row)
        
        # Clear and update
        worksheet.clear()
        worksheet.update(values=clean_values, range_name='A1')
        
        print(f"Successfully saved {len(df)} forms to Google Sheet")
        return True
        
    except Exception as e:
        print(f"ERROR saving forms to Google Sheet: {e}")
        import traceback
        traceback.print_exc()
        return False


# ============================================
# ADMIN MANAGE STUDENTS ROUTE (AUTO-ID GENERATION)
# ============================================
@app.route('/admin/manage_students_v2', methods=['GET', 'POST'])
def admin_manage_students_v2():
    """Manage students with auto-generated IDs based on department and year."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Create students file if not exists
    init_excel_db()
    
    if request.method == 'POST':
        action = request.form.get('action', '')
        
        if action == 'add':
            student_name = request.form.get('student_name', '').strip()
            department = request.form.get('department', '').strip()
            parent_phone = request.form.get('parent_phone', '').strip()
            student_form = request.form.get('student_form', '').strip()
            year = request.form.get('year', datetime.now().year)
            
            if not student_name or not department:
                flash('Student Name and Department are required.', 'danger')
                return redirect(url_for('admin_manage_students_v2'))
            
            try:
                # Generate unique student ID based on department and year
                student_id = generate_student_id(department, int(year))
                
                # Load existing students
                df_students = load_excel_data('students')
                
                # Add new student
                new_student = {
                    'id': 1 if df_students.empty else int(df_students['id'].max()) + 1,
                    'student_id': student_id,
                    'student_name': student_name,
                    'department': department,
                    'Form': student_form,
                    'parent_phone': parent_phone,
                    'created_at': datetime.now().strftime('%Y-%m-%d %H:%M')
                }
                
                df_students = pd.concat([df_students, pd.DataFrame([new_student])], ignore_index=True)
                save_excel_data('students', df_students)
                
                # Sync to Google Sheets
                try:
                    save_students_to_sheet()
                except Exception as e:
                    print(f"Error syncing students to Google Sheet: {e}")
                
                flash(f'Student "{student_name}" added successfully! ID: {student_id}', 'success')
                
            except Exception as e:
                flash(f'Error adding student: {str(e)}', 'danger')
        
        elif action == 'delete':
            student_id = request.form.get('student_id', '')
            
            try:
                df_students = load_excel_data('students')
                df_students = df_students[df_students['student_id'].astype(str) != str(student_id)]
                save_excel_data('students', df_students)
                
                # Sync to Google Sheets
                try:
                    save_students_to_sheet()
                except Exception as e:
                    print(f"Error syncing students to Google Sheet: {e}")
                
                flash('Student deleted successfully!', 'success')
            except Exception as e:
                flash(f'Error deleting student: {str(e)}', 'danger')
    
    # Load all students
    df_students = load_excel_data('students')
    students = df_students.to_dict('records') if not df_students.empty else []
    
    # Load available forms
    df_forms = load_excel_data('forms')
    forms = df_forms['FormName'].tolist() if not df_forms.empty else []
    
    return render_template('admin_manage_students.html', 
                          students=students,
                          forms=forms,
                          current_year=datetime.now().year)


def save_students_to_sheet():
    """
    Save students data to Google Sheet 'Students' worksheet.
    This function reads from the local Excel file and syncs to Google Sheets.
    """
    try:
        gc = get_google_sheet_client()
        if not gc:
            print("ERROR: Failed to get Google Sheets client for students")
            return False
        
        # Load students from local Excel
        df = load_excel_data('students')
        
        if df.empty:
            print("No students data to save")
            return True
        
        print(f"Loaded {len(df)} students from local Excel")
        
        # Open the spreadsheet
        sh = gc.open_by_key(UNIFIED_GOOGLE_SHEET_ID)
        
        # Get or create 'Students' worksheet
        try:
            worksheet = sh.worksheet('Students')
            print("Found 'Students' worksheet - will update it")
        except gspread.exceptions.WorksheetNotFound:
            print("'Students' worksheet not found - creating new one...")
            worksheet = sh.add_worksheet(title='Students', rows=1000, cols=20)
            print("Created new 'Students' worksheet")
        
        # Prepare all values - headers first, then data
        all_values = [df.columns.tolist()] + df.values.tolist()
        
        # Convert all values to strings, handle NaN
        clean_values = []
        for row in all_values:
            clean_row = []
            for val in row:
                if pd.isna(val):
                    clean_row.append('')
                elif isinstance(val, (int, float)):
                    clean_row.append(str(val))
                else:
                    clean_row.append(str(val))
            clean_values.append(clean_row)
        
        # Clear and update
        worksheet.clear()
        worksheet.update(values=clean_values, range_name='A1')
        
        print(f"Successfully saved {len(df)} students to Google Sheet")
        return True
        
    except Exception as e:
        print(f"ERROR saving students to Google Sheet: {e}")
        import traceback
        traceback.print_exc()
        return False


# ============================================
# ADMIN RESULT SETTINGS ROUTE
# ============================================
@app.route('/admin/result_settings', methods=['GET', 'POST'])
def admin_result_settings():
    """Manage result entry settings and view submissions"""
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    
    # Create settings file if not exists
    init_excel_db()
    
    if request.method == 'POST':
        is_active = request.form.get('is_active') == '1'
        deadline = request.form.get('deadline', '')
        
        df_settings = load_excel_data('settings')
        
        # Remove old result settings
        if not df_settings.empty:
            df_settings = df_settings[~df_settings['key'].str.contains('result_', na=False)]
        
        # Add new settings
        new_settings = pd.DataFrame([
            {'key': 'result_is_active', 'value': str(int(is_active)), 'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M')},
            {'key': 'result_deadline', 'value': deadline, 'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M')}
        ])
        
        if df_settings.empty:
            df_settings = new_settings
        else:
            df_settings = pd.concat([df_settings, new_settings], ignore_index=True)
        
        save_excel_data('settings', df_settings)
        flash('Result settings updated successfully!', 'success')
    
    # Load current settings
    result_settings = None
    df_settings = load_excel_data('settings')
    if not df_settings.empty:
        is_active_row = df_settings[df_settings['key'] == 'result_is_active']
        deadline_row = df_settings[df_settings['key'] == 'result_deadline']
        if not is_active_row.empty:
            result_settings = type('obj', (object,), {
                'is_active': bool(int(is_active_row['value'].values[0])),
                'deadline': deadline_row['value'].values[0] if not deadline_row.empty else ''
            })()
    
    # Load submissions
    df_submissions = load_excel_data('result_submissions')
    submissions = df_submissions.to_dict('records') if not df_submissions.empty else []
    
    pending_count = len([s for s in submissions if s.get('status') == 'pending'])
    approved_count = len([s for s in submissions if s.get('status') == 'approved'])
    
    return render_template('admin_result_settings.html', 
                          result_settings=result_settings,
                          submissions=submissions,
                          pending_count=pending_count,
                          approved_count=approved_count)



# Department codes mapping (for auto-generating student IDs)
# Format: [DEPT_CODE]STUGTI[YEAR][SEQUENCE_NUMBER] e.g., EETSTUGTI26001
DEPARTMENT_CODES = {
    # Technical Departments (Vocational)
    'electrical': 'EET',
    'electricals': 'EET',
    'electronics': 'ELT',
    'mechanical': 'MEC',
    'welding': 'WEL',
    'fashion': 'FAS',
    'garment': 'GAR',
    'plumbing': 'PLB',
    'catering': 'CAT',
    'building': 'BLD',
    'construction': 'CON',
    'wood': 'WOO',
    'hospitality': 'HOS',
    
    # Academic Departments
    'science': 'SCI',
    'general science': 'GSC',
    'arts': 'ART',
    'general arts': 'GAR',
    'business': 'BUS',
    'accounting': 'ACC',
    'agriculture': 'AGR',
    'general agric': 'GAG',
    'home science': 'HSC',
    
    # ICT and Computer
    'ict': 'ICT',
    'computer': 'COM',
    
    # Languages
    'english': 'ENG',
    'mathematics': 'MAT',
    'math': 'MAT',
    'social': 'SOC',
}

# Institution code
INSTITUTION_CODE = 'STUGTI'  # Student + Asante Tano Methodist  Technical/Vocational Institute

def generate_student_id(department, year=None):
    """
    Generate a unique student ID based on department and year.
    Format: [DEPT_CODE]STUGTI[YEAR][SEQUENCE_NUMBER]
    Example: EETSTUGTI26001
    
    Checks both local Excel file AND Google Sheets to find the next available ID.
    
    Args:
        department: The department/student belongs to
        year: Year (defaults to current year if not provided)
    
    Returns:
        A unique student ID string
    """
    if year is None:
        year = datetime.now().year
    
    # Get last 2 digits of year
    year_suffix = str(year)[-2:]
    
    # Find department code
    dept_lower = department.lower().strip()
    dept_code = 'GEN'  # Default for unknown departments
    
    for dept_name, code in DEPARTMENT_CODES.items():
        if dept_name in dept_lower:
            dept_code = code
            break
    
    # Prefix for matching
    prefix = f"{dept_code}{INSTITUTION_CODE}{year_suffix}"
    
    # Collect all existing IDs from both sources
    existing_ids = set()
    
    # 1. Check local Excel file
    try:
        df_local = load_excel_data('students')
        if not df_local.empty and 'student_id' in df_local.columns:
            for sid in df_local['student_id'].astype(str).values:
                if str(sid).startswith(prefix):
                    existing_ids.add(str(sid))
    except Exception as e:
        print(f"Error checking local students: {e}")
    
    # 2. Check Google Sheets (main results sheet)
    try:
        df_sheet = load_results_from_sheet()
        if not df_sheet.empty and 'Student ID' in df_sheet.columns:
            for sid in df_sheet['Student ID'].astype(str).values:
                if str(sid).startswith(prefix):
                    existing_ids.add(str(sid))
    except Exception as e:
        print(f"Error checking Google Sheets: {e}")
    
    # Find the next available sequence number
    next_seq = 1
    while True:
        candidate_id = f"{prefix}{next_seq:03d}"
        if candidate_id not in existing_ids:
            break
        next_seq += 1
        # Safety limit to prevent infinite loop
        if next_seq > 99999:
            print(f"WARNING: Sequence number exceeded limit for {prefix}")
            break
    
    return candidate_id


# ============================================
# INSTRUCTOR PRINT STUDENTS ROUTE
# ============================================
@app.route('/instructor/print_students', methods=['GET'])
def instructor_print_students():
    """Route for instructors to print their student list"""
    if not session.get('instructor_logged_in'):
        return redirect(url_for('instructor_login'))
    
    instructor_name = session.get('instructor_name', '')
    assigned_subject = session.get('assigned_subject', '')
    assigned_forms = session.get('assigned_forms', [])
    
    # Load students from assigned forms
    df_students = load_excel_data('students')
    
    if assigned_forms:
        students = df_students[df_students['Form'].isin(assigned_forms)].to_dict('records')
    else:
        students = []
    
    return render_template('instructor_print_students.html',
                         instructor_name=instructor_name,
                         students=students,
                         assigned_subject=assigned_subject,
                         assigned_forms=assigned_forms)



if __name__ == '__main__':
    # In a production environment, use a production-ready WSGI server like Gunicorn or uWSGI
    # For local development, this is fine
    # Set debug=False for production
    app.run(debug=True)
