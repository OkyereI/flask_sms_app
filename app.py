from flask import Flask, render_template, request, redirect, url_for, flash, session, make_response, send_from_directory, send_file
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

# --- Import shared SQLAlchemy instance from extensions ---
from extensions import db  # Use the shared db instance to avoid duplicate registration

app = Flask(__name__)

# =============================================================================
# DATABASE CONFIGURATION - Using External PostgreSQL on Render
# =============================================================================
# External PostgreSQL Database URL from Render
# Format: postgresql://username:password@host:port/database_name
EXTERNAL_DATABASE_URL = 'postgresql://bisinessdb_user:QceRMwRe2FtjhPk8iMLCIKB3j3s4KmhI@dpg-d1olvgbuibrs73cum700-a.oregon-postgres.render.com/bisinessdb'

# Configure Flask app with the external PostgreSQL database
app.config['SQLALCHEMY_DATABASE_URI'] = EXTERNAL_DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'your_very_secret_key_replace_this'

# Initialize the shared SQLAlchemy instance with this Flask app
db.init_app(app)

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
GOOGLE_SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vR6QYC30lQNjpJjHPJFUG6XUUZqP5XfnNjBUB4Xrhb7pzFP87-IF_2_iRAdJKCUk5zJThu-ml1hzyFm/pub?output=csv"

# Alternative: Using the sheet ID (if you prefer gviz format)
GOOGLE_SHEET_ID = "1JYs4ZtUKfklu-bEqdYOeeKu6nF7rM5I55EQZb-yrs-A"  # Your Google Sheet ID
GOOGLE_SHEET_NAME = "Sheet1"  # Sheet name in your Google Sheet

# =============================================================================
# DATABASE SETUP FOR RENDER
# =============================================================================
# RENDER DEPLOYMENT OPTIONS:
# 
# Option 1: SQLite (Current - Simplest)
# - Works on Render's ephemeral filesystem
# - Data persists while service is running
# - Data may be lost during service restarts/redeployments
# - No additional setup required
#
# Option 2: PostgreSQL (Recommended for Production)
# - Data persists across deployments
# - Better performance and reliability
# - Add PostgreSQL add-on in Render dashboard
# - Update DATABASE_URL environment variable in Render
# 
# To use PostgreSQL on Render:
# 1. In Render dashboard, go to your service
# 2. Click "Environment" → "Add Environment Variable"
# 3. Add: DATABASE_URL with your PostgreSQL connection string
# 4. Uncomment the PostgreSQL configuration below
# =============================================================================

# Database Configuration
# SQLite (default - works on Render)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///school_management.db'

# PostgreSQL Configuration (uncomment for Render with PostgreSQL add-on)
# import os
# database_url = os.environ.get('DATABASE_URL')
# if database_url:
#     # Heroku/Render PostgreSQL format requires sslmode='require'
#     if 'postgres://' in database_url:
#         database_url = database_url.replace('postgres://', 'postgresql://', 1)
#     app.config['SQLALCHEMY_DATABASE_URI'] = database_url
# else:
#     # Fallback to SQLite if no DATABASE_URL
#     app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///school_management.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'your_very_secret_key_replace_this'

# Initialize the shared SQLAlchemy instance with this Flask app
# db.init_app(app)

# Arkesel API Configuration
# IMPORTANT: Double-check that this API key is correct and active in your Arkesel account.
# Using the API key provided by the user for the older endpoint.
ARKESEL_API_KEY = "b0FrYkNNVlZGSmdrendVT3hwUHk"
# Using the older GET-based SMS send URL provided by the user.
ARKESEL_SMS_URL = "https://sms.arkesel.com/sms/api"
# IMPORTANT: Replace with your registered Arkesel Sender ID.
# Verify this Sender ID is registered and approved in your Arkesel account.
ARKESEL_SENDER_ID = "GyedTuech" # e.g., "MySchool"

# --- Google Sheets API Configuration for Writing ---
# Path to your service account credentials JSON file
# You need to download this from Google Cloud Console
SERVICE_ACCOUNT_FILE = 'service_account_credentials.json'
# Define the scope for Google Sheets API
SCOPES = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']

def get_google_sheet_client():
    """Authenticates and returns a Google Sheets client."""
    try:
        credentials = ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_FILE, SCOPES)
        gc = gspread.authorize(credentials)
        return gc
    except Exception as e:
        print(f"Error authenticating with Google Sheets API: {e}")
        return None

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
    """Add a new HOD account with hashed password."""
    HOD_CREDENTIALS[username] = {
        'password': generate_password_hash(password),
        'department': department
    }

# Initialize with default HOD accounts for testing
# Add some sample HOD accounts (admin can add more via admin panel)
add_hod('hod_electricals', 'elec2025', 'electricals')
add_hod('hod_welding', 'weld2025', 'welding')
add_hod('hod_business', 'biz2025', 'Business')

print(f"HOD accounts initialized: {list(HOD_CREDENTIALS.keys())}")

# ============================================================
# DATABASE MODELS FOR NEW MODULES (Store, Finance, Estate)
# IMPORTANT: All tables use 'school_' prefix to avoid conflicts with existing business tables
# =============================================================================

# --- STORE MODELS ---
class SchoolStoreItem(db.Model):
    """Model for school store inventory items"""
    __tablename__ = 'school_store_items'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50), nullable=False)  # Food, Stationery, Uniform, Equipment, etc.
    unit = db.Column(db.String(30), nullable=False)  # kg, pieces, bags, liters, etc.
    quantity = db.Column(db.Float, default=0)
    min_threshold = db.Column(db.Float, default=0)  # Alert when below this
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

class SchoolStoreTransaction(db.Model):
    """Model for school store transactions (in/out)"""
    __tablename__ = 'school_store_transactions'
    
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey('school_store_items.id'), nullable=False)
    transaction_type = db.Column(db.String(10), nullable=False)  # IN (restock), OUT (issue)
    quantity = db.Column(db.Float, nullable=False)
    recipient = db.Column(db.String(100))  # Student name, department, etc.
    recipient_type = db.Column(db.String(50))  # Student, Staff, Department, Class
    notes = db.Column(db.Text)
    issued_by = db.Column(db.String(100))  # Admin/Store manager name
    created_at = db.Column(db.DateTime, default=datetime.now)

class SchoolSupplier(db.Model):
    """Model for school suppliers"""
    __tablename__ = 'school_suppliers'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    contact_person = db.Column(db.String(100))
    phone = db.Column(db.String(20))
    email = db.Column(db.String(100))
    address = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now)

# --- FINANCE MODELS ---
class SchoolFeeType(db.Model):
    """Model for fee types (e.g., Tuition, Feeding, PTA)"""
    __tablename__ = 'school_fee_types'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    amount = db.Column(db.Float, nullable=False)
    academic_year = db.Column(db.String(10), nullable=False)  # e.g., 2025-2026
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

class SchoolStudentAccount(db.Model):
    """Model for student financial accounts"""
    __tablename__ = 'school_student_accounts'
    
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(50), nullable=False)  # Student ID from Google Sheets
    student_name = db.Column(db.String(100), nullable=False)
    total_billed = db.Column(db.Float, default=0)
    total_paid = db.Column(db.Float, default=0)
    balance = db.Column(db.Float, default=0)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

class SchoolPayment(db.Model):
    """Model for school payment transactions"""
    __tablename__ = 'school_payments'
    
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(50), nullable=False)
    student_name = db.Column(db.String(100), nullable=False)
    fee_type_id = db.Column(db.Integer, db.ForeignKey('school_fee_types.id'))
    amount = db.Column(db.Float, nullable=False)
    payment_method = db.Column(db.String(30), nullable=False)  # Cash, Mobile Money, Bank
    reference_number = db.Column(db.String(50))  # Transaction ID, Receipt number
    received_by = db.Column(db.String(100), nullable=False)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now)

class SchoolExpense(db.Model):
    """Model for school expenses"""
    __tablename__ = 'school_expenses'
    
    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(50), nullable=False)  # Maintenance, Supplies, Food, etc.
    description = db.Column(db.Text, nullable=False)
    amount = db.Column(db.Float, nullable=False)
    vendor = db.Column(db.String(100))
    approved_by = db.Column(db.String(100))
    receipt_number = db.Column(db.String(50))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now)

# --- ESTATE MODELS ---
class SchoolLocation(db.Model):
    """Model for school locations"""
    __tablename__ = 'school_locations'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)  # Block A, Science Lab, Dining Hall
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now)

class SchoolAsset(db.Model):
    """Model for school assets/property"""
    __tablename__ = 'school_assets'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    asset_code = db.Column(db.String(50), unique=True)  # QR code or asset tag
    category = db.Column(db.String(50), nullable=False)  # Furniture, Electronics, Building, Equipment
    location_id = db.Column(db.Integer, db.ForeignKey('school_locations.id'))
    purchase_date = db.Column(db.Date)
    purchase_value = db.Column(db.Float)
    current_value = db.Column(db.Float)
    condition = db.Column(db.String(20), default='Good')  # Good, Fair, Poor, Condemned
    status = db.Column(db.String(20), default='Active')  # Active, Lost, Damaged, Disposed
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    # Relationship
    location = db.relationship('SchoolLocation', backref='school_assets')

class SchoolAssetMovement(db.Model):
    """Model for tracking school asset movements/locations"""
    __tablename__ = 'school_asset_movements'
    
    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey('school_assets.id'), nullable=False)
    from_location_id = db.Column(db.Integer, db.ForeignKey('school_locations.id'))
    to_location_id = db.Column(db.Integer, db.ForeignKey('school_locations.id'), nullable=False)
    moved_by = db.Column(db.String(100), nullable=False)
    reason = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now)

class SchoolMaintenanceRequest(db.Model):
    """Model for school maintenance requests"""
    __tablename__ = 'school_maintenance_requests'
    
    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey('school_assets.id'))
    location_id = db.Column(db.Integer, db.ForeignKey('school_locations.id'))
    issue_description = db.Column(db.Text, nullable=False)
    priority = db.Column(db.String(20), default='Medium')  # Low, Medium, High, Urgent
    status = db.Column(db.String(20), default='Reported')  # Reported, In Progress, Completed
    reported_by = db.Column(db.String(100))
    assigned_to = db.Column(db.String(100))
    estimated_cost = db.Column(db.Float)
    actual_cost = db.Column(db.Float)
    contractor = db.Column(db.String(100))
    completed_date = db.Column(db.Date)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now)

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
    'electricals': ['Electrical installation', 'Principles of electrical','Practicals'],
    'welding': ['Welding Fabrication', 'Welding Principles','Practicals'],
    'fashion': ['Garment design', 'Garment Construction','Fashion Illustration', 'Practicals'],
    'plumbing': ['Plumbing Principles', 'Plumbing Technology','Practicals'],
    'Catering': ['Catering Principles', 'Catering Production','Practicals'],
    'building': ['Construction Practice', 'Construction Materials','Practicals'],
    'wood': ['Wood Principles', 'Wood Technology','Practicals'],
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
def load_results_from_sheet():
    """
    Loads the student results from Google Sheets.
    Returns a pandas DataFrame with the student data.
    """
    try:
        if DATA_SOURCE == 'google_sheet':
            # Read from Google Sheet CSV export
            print(f"Loading data from Google Sheet: {GOOGLE_SHEET_CSV_URL}")
            df = pd.read_csv(GOOGLE_SHEET_CSV_URL)
            print(f"Successfully loaded {len(df)} rows from Google Sheet")
        else:
            print(f"Unknown DATA_SOURCE: {DATA_SOURCE}")
            return pd.DataFrame()
        
        # Clean up column names by removing leading/trailing spaces
        df.columns = df.columns.str.strip()

        # --- Verify essential columns exist immediately after loading ---
        essential_cols = [COLUMN_MAPPING.get('Student ID'), COLUMN_MAPPING.get('Student Name'), COLUMN_MAPPING.get('Parent Phone')]
        if COLUMN_MAPPING.get('Student Department'):
            essential_cols.append(COLUMN_MAPPING.get('Student Department'))
        
        # Filter out None values from essential_cols if a mapping wasn't found
        essential_cols = [col for col in essential_cols if col is not None]

        if not all(col in df.columns for col in essential_cols):
            missing = [col for col in essential_cols if col not in df.columns]
            print(f"Error: Missing ESSENTIAL columns in sheet: {missing}")
            return pd.DataFrame()

        # --- Fix for phone numbers ending in .0 ---
        if COLUMN_MAPPING.get('Parent Phone') and COLUMN_MAPPING['Parent Phone'] in df.columns:
            df[COLUMN_MAPPING['Parent Phone']] = df[COLUMN_MAPPING['Parent Phone']].astype(str).str.replace('.0', '', regex=False).str.strip()
            print("Cleaned Parent Phone column")

        print(f"Successfully loaded data from Google Sheet. Total students: {len(df)}")
        return df
    except Exception as e:
        print(f"Error loading data from Google Sheet: {e}")
        print("Please verify:")
        print("1. The Google Sheet is published to web (File → Share → Publish to web)")
        print("2. The CSV export URL is correct")
        print("3. The sheet contains the expected column headers")
        return pd.DataFrame()


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

# Helper function to get all results for a student (needs implementation)
def get_all_student_results_by_id(student_id):
    """
    Fetches all results (core and relevant electives) for a given student_id,
    across all available years and semesters.
    Returns a dictionary structured for easy display.
    Example:
    {
        'student_info': {...},
        'results_by_semester': {
            '2025 - Semester 1': {
                'Core Subjects': [
                    {'Subject': 'Math', 'Exams Score': 78, 'Class Score': 20, 'Total Score': 98, 'Remarks': 'Good', 'Grade': 'A'},
                    {'Subject': 'English', ...}
                ],
                'Elective Subjects': [
                    {'Subject': 'Physics', ...}
                ]
            },
            '2025 - Semester 1': {...}
        }
    }
    """
    df = load_results_from_sheet()
    if df.empty:
        return {'student_info': {}, 'results_by_semester': {}}

    student_row_series = df[df[COLUMN_MAPPING['Student ID']] == student_id.strip()]

    if student_row_series.empty:
        return {'student_info': {}, 'results_by_semester': {}}

    student_row = student_row_series.iloc[0].to_dict()

    student_department = student_row.get(COLUMN_MAPPING.get('Student Department'), 'N/A')
    
    results_by_semester = {}

    # Get the specific electives for the student's department
    student_electives_list = ELECTIVE_SUBJECT_NAMES_BY_DEPARTMENT.get(student_department, [])

    # Iterate through all discovered semesters and years
    for semester_key in sorted(FULL_SUBJECT_DETAILS_BY_SEMESTER_KEY.keys()):
        subjects_in_semester_template = FULL_SUBJECT_DETAILS_BY_SEMESTER_KEY.get(semester_key, {})
        
        core_subjects_data = []
        elective_subjects_data = []
        has_data_for_semester = False

        # Process Core Subjects
        for subject_name, details in subjects_in_semester_template.get('Core Subjects', {}).items():
            subject_info = {'Subject': subject_name}
            found_subject_data = False
            for score_type_display, col_name in details.items():
                score_value = student_row.get(col_name)
                if pd.notna(score_value) and str(score_value).strip() != '' and str(score_value).strip().upper() != 'N/A':
                    subject_info[score_type_display] = score_value
                    found_subject_data = True
                    has_data_for_semester = True
                else:
                    subject_info[score_type_display] = 'N/A'
            if found_subject_data:
                core_subjects_data.append(subject_info)

        # Process Elective Subjects (filter by student's department)
        for subject_name, details in subjects_in_semester_template.get('Elective Subjects', {}).items():
            if subject_name in student_electives_list:
                subject_info = {'Subject': subject_name}
                found_subject_data = False
                for score_type_display, col_name in details.items():
                    score_value = student_row.get(col_name)
                    if pd.notna(score_value) and str(score_value).strip() != '' and str(score_value).strip().upper() != 'N/A':
                        subject_info[score_type_display] = score_value
                        found_subject_data = True
                        has_data_for_semester = True
                    else:
                        subject_info[score_type_display] = 'N/A'
                if found_subject_data:
                    elective_subjects_data.append(subject_info)
        
        if has_data_for_semester:
            results_by_semester[semester_key] = {
                'Core Subjects': core_subjects_data,
                'Elective Subjects': elective_subjects_data
            }

    return {
        'student_info': {
            'Student ID': student_row.get(COLUMN_MAPPING.get('Student ID'), 'N/A'),
            'Student Name': student_row.get(COLUMN_MAPPING.get('Student Name'), 'N/A'),
            'Parent Phone': student_row.get(COLUMN_MAPPING.get('Parent Phone'), 'N/A'),
            'Student Department': student_row.get(COLUMN_MAPPING.get('Student Department'), 'N/A'),
            'HOD Remarks': student_row.get('HOD Remarks', '')  # Include HOD Remarks from Google Sheet
        },
        'results_by_semester': results_by_semester
    }


@app.route('/admin')
def admin_dashboard():
    """Admin dashboard to view all results and trigger SMS (protected)."""
    # Check if admin is logged in
    if not session.get('admin_logged_in'):
        flash('Please log in to access the admin dashboard.', 'warning')
        return redirect(url_for('admin_login'))

    df = load_results_from_sheet()
    if df.empty:
        return render_template('admin.html', error="Could not load results data for admin view. Check sheet access.")

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
    student_data_for_template = []
    for index, row in df.iterrows():
        student_data_for_template.append({
            'Student ID': row.get(COLUMN_MAPPING.get('Student ID'), 'N/A'),
            'Student Name': row.get(COLUMN_MAPPING.get('Student Name'), 'N/A'),
            'Parent Phone': row.get(COLUMN_MAPPING.get('Parent Phone'), 'N/A'),
            'Student Department': row.get(COLUMN_MAPPING.get('Student Department'), 'N/A')
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
            sh = gc.open_by_key(SHEET_ID)
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
            sh = gc.open_by_key(SHEET_ID)
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
            elif username in HOD_CREDENTIALS:
                flash('Username already exists.', 'danger')
            else:
                add_hod(username, password, department)
                flash(f'HOD account for {department} created successfully!', 'success')
        
        elif action == 'delete':
            username = request.form.get('username')
            if username in HOD_CREDENTIALS:
                del HOD_CREDENTIALS[username]
                flash(f'HOD account "{username}" deleted successfully.', 'success')
            else:
                flash('HOD account not found.', 'danger')
    
    # Get list of HOD accounts with department info
    hod_list = []
    for username, data in HOD_CREDENTIALS.items():
        hod_list.append({
            'username': username,
            'department': data['department']
        })
    
    return render_template('manage_hods.html', 
                           hods=hod_list,
                           available_departments=AVAILABLE_DEPARTMENTS)


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
                            sh = gc.open_by_key(SHEET_ID)
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


# --- Route: Upload Excel Results (Wide Format - All Subjects in One Row) ---
@app.route('/admin/upload_excel_results', methods=['GET', 'POST'])
def upload_excel_results():
    """Allows admin to upload Excel files with student results using wide format (all subjects in one row per student)."""
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
                
                # Load existing data from Google Sheet
                existing_df = load_results_from_sheet()
                if existing_df.empty:
                    flash('Could not load existing student data from database.', 'danger')
                    return redirect(request.url)
                
                student_id_col = COLUMN_MAPPING.get('Student ID')
                if not student_id_col or student_id_col not in existing_df.columns:
                    flash('Student ID column not found in the main database.', 'danger')
                    return redirect(request.url)
                
                # Prepare existing data for matching
                existing_df[student_id_col] = existing_df[student_id_col].astype(str).str.strip()
                
                # Get Google Sheets client for writing
                gc = get_google_sheet_client()
                if not gc:
                    flash('Could not connect to Google Sheets. Please try again later.', 'danger')
                    return redirect(request.url)
                
                try:
                    sh = gc.open_by_key(SHEET_ID)
                    worksheet = sh.sheet1
                    all_records = worksheet.get_all_records()
                    
                    if all_records:
                        sheet_headers = list(all_records[0].keys())
                    else:
                        sheet_headers = []
                    
                except Exception as e:
                    flash(f'Error accessing Google Sheet: {e}', 'danger')
                    return redirect(request.url)
                
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
                        
                        # Get the row index in the sheet (1-indexed for header, so add 2)
                        sheet_row_idx = matching_rows.index[0] + 2
                        
                        # Process each column that matches score columns
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
                            
                            # Find column index in sheet (0-indexed, convert to 1-indexed)
                            if col_name in sheet_headers:
                                col_idx = sheet_headers.index(col_name) + 1
                                
                                # Update the cell
                                try:
                                    worksheet.update_cell(sheet_row_idx, col_idx, value)
                                except Exception as e:
                                    print(f"Error updating cell for student {student_id}, column {col_name}: {e}")
                        
                        updated_count += 1
                        
                    except Exception as e:
                        error_messages.append(f"Row {index + 1}: {str(e)}")
                        skipped_count += 1
                
                # Refresh metadata to include any new columns
                initialize_sheet_metadata()
                
                # Report results
                if updated_count > 0:
                    semesters_processed = ', '.join(sorted(detected_semesters))
                    flash(f'Successfully updated {updated_count} student records for {semesters_processed}.', 'success')
                
                if skipped_count > 0:
                    flash(f'Skipped {skipped_count} records (missing Student ID or student not found in database).', 'warning')
                
                if error_messages:
                    for error in error_messages[:3]:  # Show first 3 errors
                        flash(f'Error: {error}', 'danger')
                
                return redirect(url_for('admin_dashboard'))
                
            except Exception as e:
                flash(f'Error processing Excel file: {e}', 'danger')
                print(f"Excel processing error: {e}")
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
    """Store management dashboard."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Get statistics
    total_items = SchoolStoreItem.query.count()
    low_stock = SchoolStoreItem.query.filter(SchoolStoreItem.quantity <= SchoolStoreItem.min_threshold).count()
    today_transactions = SchoolStoreTransaction.query.filter(
        db.func.date(SchoolStoreTransaction.created_at) == datetime.now().date()
    ).count()
    
    recent_transactions = SchoolStoreTransaction.query.order_by(
        SchoolStoreTransaction.created_at.desc()
    ).limit(10).all()
    
    # Pre-fetch item data for template display
    items_dict = {item.id: item for item in SchoolStoreItem.query.all()}
    
    return render_template('admin_store.html',
                           total_items=total_items,
                           low_stock=low_stock,
                           today_transactions=today_transactions,
                           recent_transactions=recent_transactions,
                           items_dict=items_dict,
                           StoreItem=SchoolStoreItem)


@app.route('/admin/store/inventory')
def admin_store_inventory():
    """View and manage store inventory."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    items = SchoolStoreItem.query.order_by(SchoolStoreItem.category, SchoolStoreItem.name).all()
    return render_template('admin_store_inventory.html', items=items)


@app.route('/admin/store/add_item', methods=['GET', 'POST'])
def admin_store_add_item():
    """Add new item to store inventory."""
    if not session.get('admin_logged_in'):
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
            new_item = SchoolStoreItem(
                name=name,
                category=category,
                unit=unit,
                quantity=quantity,
                min_threshold=min_threshold
            )
            db.session.add(new_item)
            db.session.commit()
            flash(f'Item "{name}" added successfully!', 'success')
            return redirect(url_for('admin_store_inventory'))
    
    return render_template('admin_store_add_item.html')


@app.route('/admin/store/restock/<int:item_id>', methods=['GET', 'POST'])
def admin_store_restock(item_id):
    """Restock an existing item."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    item = SchoolStoreItem.query.get_or_404(item_id)
    
    if request.method == 'POST':
        quantity = float(request.form.get('quantity', 0))
        recipient = request.form.get('recipient', '')
        notes = request.form.get('notes', '')
        
        if quantity <= 0:
            flash('Quantity must be greater than 0.', 'warning')
        else:
            # Update item quantity
            item.quantity += quantity
            item.updated_at = datetime.now()
            
            # Record transaction
            transaction = SchoolStoreTransaction(
                item_id=item.id,
                transaction_type='IN',
                quantity=quantity,
                recipient=recipient,
                recipient_type='Supplier',
                notes=notes,
                issued_by=session.get('admin_username', 'Admin')
            )
            db.session.add(transaction)
            db.session.commit()
            
            flash(f'Successfully restocked {quantity} {item.unit} of {item.name}!', 'success')
            return redirect(url_for('admin_store_inventory'))
    
    return render_template('admin_store_restock.html', item=item)


@app.route('/admin/store/issue', methods=['GET', 'POST'])
def admin_store_issue():
    """Issue items from store."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    if request.method == 'POST':
        item_id = request.form.get('item_id')
        quantity = float(request.form.get('quantity', 0))
        recipient = request.form.get('recipient')
        recipient_type = request.form.get('recipient_type')
        notes = request.form.get('notes', '')
        
        item = SchoolStoreItem.query.get(item_id)
        
        if not item:
            flash('Item not found.', 'danger')
        elif quantity <= 0:
            flash('Quantity must be greater than 0.', 'warning')
        elif item.quantity < quantity:
            flash(f'Insufficient stock! Available: {item.quantity} {item.unit}', 'danger')
        else:
            # Deduct from inventory
            item.quantity -= quantity
            item.updated_at = datetime.now()
            
            # Record transaction
            transaction = SchoolStoreTransaction(
                item_id=item.id,
                transaction_type='OUT',
                quantity=quantity,
                recipient=recipient,
                recipient_type=recipient_type,
                notes=notes,
                issued_by=session.get('admin_username', 'Admin')
            )
            db.session.add(transaction)
            db.session.commit()
            
            flash(f'Successfully issued {quantity} {item.unit} of {item.name} to {recipient}!', 'success')
            return redirect(url_for('admin_store'))
    
    items = SchoolStoreItem.query.order_by(SchoolStoreItem.name).all()
    return render_template('admin_store_issue.html', items=items)


@app.route('/admin/store/transactions')
def admin_store_transactions():
    """View all store transactions."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    transactions = SchoolStoreTransaction.query.order_by(
        SchoolStoreTransaction.created_at.desc()
    ).limit(100).all()
    
    return render_template('admin_store_transactions.html', transactions=transactions)


# --- FINANCE MODULE ROUTES ---

@app.route('/admin/finance')
def admin_finance():
    """Finance management dashboard."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Get statistics
    total_revenue = db.session.query(db.func.sum(SchoolPayment.amount)).scalar() or 0
    total_expenses = db.session.query(db.func.sum(SchoolExpense.amount)).scalar() or 0
    balance = total_revenue - total_expenses
    
    # Get debtors count
    debtors_count = SchoolStudentAccount.query.filter(SchoolStudentAccount.balance > 0).count()
    total_outstanding = db.session.query(db.func.sum(SchoolStudentAccount.balance)).scalar() or 0
    
    recent_payments = SchoolPayment.query.order_by(SchoolPayment.created_at.desc()).limit(10).all()
    
    return render_template('admin_finance.html',
                           total_revenue=total_revenue,
                           total_expenses=total_expenses,
                           balance=balance,
                           debtors_count=debtors_count,
                           total_outstanding=total_outstanding,
                           recent_payments=recent_payments)


@app.route('/admin/finance/fee_setup', methods=['GET', 'POST'])
def admin_finance_fee_setup():
    """Setup fee types and amounts."""
    if not session.get('admin_logged_in'):
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
            new_fee = SchoolFeeType(
                name=name,
                description=description,
                amount=amount,
                academic_year=academic_year
            )
            db.session.add(new_fee)
            db.session.commit()
            flash(f'Fee type "{name}" created successfully!', 'success')
            return redirect(url_for('admin_finance_fee_setup'))
    
    fees = SchoolFeeType.query.order_by(SchoolFeeType.academic_year, SchoolFeeType.name).all()
    return render_template('admin_finance_fee_setup.html', fees=fees)


@app.route('/admin/finance/payment', methods=['GET', 'POST'])
def admin_finance_payment():
    """Record payment from student."""
    if not session.get('admin_logged_in'):
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
            # Record payment
            payment = SchoolPayment(
                student_id=student_id,
                student_name=student_name,
                fee_type_id=fee_type_id,
                amount=amount,
                payment_method=payment_method,
                reference_number=reference_number,
                received_by=session.get('admin_username', 'Admin'),
                notes=notes
            )
            db.session.add(payment)
            
            # Update student account
            account = SchoolStudentAccount.query.filter_by(student_id=student_id).first()
            if account:
                account.total_paid += amount
                account.balance -= amount
                account.updated_at = datetime.now()
            else:
                # Create new account if doesn't exist
                account = SchoolStudentAccount(
                    student_id=student_id,
                    student_name=student_name,
                    total_paid=amount,
                    balance=-amount  # Negative because they paid
                )
                db.session.add(account)
            
            db.session.commit()
            flash(f'Payment of GHS {amount} recorded for {student_name}!', 'success')
            return redirect(url_for('admin_finance'))
    
    fees = SchoolFeeType.query.filter_by(is_active=True).all()
    return render_template('admin_finance_payment.html', fees=fees)


@app.route('/admin/finance/students')
def admin_finance_students():
    """View student accounts/debtors."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    search_query = request.args.get('search', '')
    filter_type = request.args.get('filter', 'all')
    
    query = SchoolStudentAccount.query
    
    if search_query:
        query = query.filter(
            db.or_(
                SchoolStudentAccount.student_id.contains(search_query),
                SchoolStudentAccount.student_name.contains(search_query)
            )
        )
    
    if filter_type == 'debtors':
        query = query.filter(SchoolStudentAccount.balance > 0)
    elif filter_type == 'paid':
        query = query.filter(SchoolStudentAccount.balance <= 0)
    
    students = query.order_by(SchoolStudentAccount.student_name).all()
    return render_template('admin_finance_students.html', students=students, search_query=search_query, filter_type=filter_type)


@app.route('/admin/finance/expenses', methods=['GET', 'POST'])
def admin_finance_expenses():
    """Record and view expenses."""
    if not session.get('admin_logged_in'):
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
            expense = SchoolExpense(
                category=category,
                description=description,
                amount=amount,
                vendor=vendor,
                approved_by=approved_by,
                notes=notes
            )
            db.session.add(expense)
            db.session.commit()
            flash(f'Expense of GHS {amount} recorded successfully!', 'success')
            return redirect(url_for('admin_finance_expenses'))
    
    expenses = SchoolExpense.query.order_by(SchoolExpense.created_at.desc()).limit(100).all()
    total_expenses = db.session.query(db.func.sum(SchoolExpense.amount)).scalar() or 0
    
    return render_template('admin_finance_expenses.html', expenses=expenses, total_expenses=total_expenses)


@app.route('/admin/finance/collect_payment', methods=['GET', 'POST'])
def admin_finance_collect_payment():
    """Collect payment from student."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Get fee categories for dropdown
    fee_categories = SchoolFeeType.query.all()
    
    # Generate next receipt number
    last_receipt = SchoolPayment.query.order_by(SchoolPayment.id.desc()).first()
    if last_receipt:
        last_num = int(last_receipt.receipt_number.split('-')[-1])
        next_receipt_number = f'REC-{datetime.now().year}-{last_num + 1:03d}'
    else:
        next_receipt_number = f'REC-{datetime.now().year}-001'
    
    if request.method == 'POST':
        student_id = request.form.get('student_id')
        fee_type_id = request.form.get('fee_category')
        amount = float(request.form.get('payment_amount', 0))
        payment_method = request.form.get('payment_method')
        payment_date = request.form.get('payment_date')
        transaction_ref = request.form.get('transaction_ref', '')
        notes = request.form.get('payment_notes', '')
        
        if not student_id or not fee_type_id or not amount or not payment_method or not payment_date:
            flash('Please fill in all required fields.', 'warning')
        else:
            fee_type = SchoolFeeType.query.get(fee_type_id)
            if fee_type:
                payment = SchoolPayment(
                    student_id=student_id,
                    fee_type_id=fee_type_id,
                    amount=amount,
                    payment_method=payment_method,
                    payment_date=datetime.strptime(payment_date, '%Y-%m-%d').date(),
                    transaction_ref=transaction_ref,
                    receipt_number=next_receipt_number,
                    notes=notes,
                    status='completed'
                )
                db.session.add(payment)
                db.session.commit()
                flash(f'Payment of GHS {amount} recorded successfully! Receipt: {next_receipt_number}', 'success')
                return redirect(url_for('admin_finance_collect_payment'))
    
    return render_template('admin_finance_collect_payment.html', 
                           fee_categories=fee_categories, 
                           next_receipt_number=next_receipt_number)


@app.route('/admin/finance/view_payments')
def admin_finance_view_payments():
    """View all payment records."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Get all payments with related data
    payments = SchoolPayment.query.order_by(SchoolPayment.payment_date.desc(), SchoolPayment.created_at.desc()).limit(500).all()
    total_collected = db.session.query(db.func.sum(SchoolPayment.amount)).scalar() or 0
    
    return render_template('admin_finance_view_payments.html', 
                           payments=payments, 
                           total_collected=total_collected)


@app.route('/admin/finance/student_account')
def admin_finance_student_account():
    """View individual student account details."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    search_query = request.args.get('search', '')
    
    return render_template('admin_finance_student_account.html', search_query=search_query)


@app.route('/admin/finance/reports')
def admin_finance_reports():
    """Financial reports and analytics."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Calculate statistics
    total_revenue = db.session.query(db.func.sum(SchoolPayment.amount)).scalar() or 0
    total_expenses = db.session.query(db.func.sum(SchoolExpense.amount)).scalar() or 0
    pending_payments = db.session.query(db.func.count(SchoolPayment.id)).scalar() or 0
    
    # Get payments by month for chart data
    monthly_payments = db.session.query(
        db.func.date_format(SchoolPayment.payment_date, '%Y-%m').label('month'),
        db.func.sum(SchoolPayment.amount).label('total')
    ).group_by('month').order_by('month desc').limit(6).all()
    
    return render_template('admin_finance_reports.html',
                           total_revenue=total_revenue,
                           total_expenses=total_expenses,
                           pending_payments=pending_payments,
                           monthly_payments=monthly_payments)


# --- ESTATE MODULE ROUTES ---

@app.route('/admin/estate')
def admin_estate():
    """Estate management dashboard."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    # Get statistics
    total_assets = SchoolAsset.query.count()
    active_assets = SchoolAsset.query.filter_by(status='Active').count()
    pending_maintenance = SchoolMaintenanceRequest.query.filter(
        SchoolMaintenanceRequest.status.in_(['Reported', 'In Progress'])
    ).count()
    
    recent_assets = SchoolAsset.query.order_by(SchoolAsset.created_at.desc()).limit(10).all()
    
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
            existing = SchoolLocation.query.filter_by(name=name).first()
            if existing:
                flash(f'Location "{name}" already exists.', 'warning')
            else:
                location = SchoolLocation(name=name, description=description)
                db.session.add(location)
                db.session.commit()
                flash(f'Location "{name}" added successfully!', 'success')
    
    locations = SchoolLocation.query.order_by(SchoolLocation.name).all()
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
            existing = SchoolAsset.query.filter_by(asset_code=asset_code).first()
            if existing:
                flash(f'Asset code "{asset_code}" already exists.', 'warning')
            else:
                asset = SchoolAsset(
                    name=name,
                    asset_code=asset_code,
                    category=category,
                    location_id=location_id,
                    purchase_value=purchase_value,
                    current_value=purchase_value,
                    condition=condition,
                    notes=notes
                )
                db.session.add(asset)
                db.session.commit()
                flash(f'Asset "{name}" registered successfully!', 'success')
    
    search = request.args.get('search', '')
    category_filter = request.args.get('category', '')
    
    query = SchoolAsset.query
    
    if search:
        query = query.filter(
            db.or_(
                SchoolAsset.name.contains(search),
                SchoolAsset.asset_code.contains(search)
            )
        )
    
    if category_filter:
        query = query.filter_by(category=category_filter)
    
    assets = query.order_by(SchoolAsset.name).all()
    locations = SchoolLocation.query.all()
    
    return render_template('admin_estate_assets.html', assets=assets, locations=locations, 
                           search=search, category_filter=category_filter)


@app.route('/admin/estate/move/<int:asset_id>', methods=['GET', 'POST'])
def admin_estate_move_asset(asset_id):
    """Move asset to new location."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    asset = SchoolAsset.query.get_or_404(asset_id)
    
    if request.method == 'POST':
        new_location_id = request.form.get('location_id')
        reason = request.form.get('reason', '')
        
        if not new_location_id:
            flash('Please select a new location.', 'warning')
        else:
            from_location_id = asset.location_id
            
            # Update asset location
            asset.location_id = new_location_id
            asset.updated_at = datetime.now()
            
            # Record movement
            movement = SchoolAssetMovement(
                asset_id=asset.id,
                from_location_id=from_location_id,
                to_location_id=new_location_id,
                moved_by=session.get('admin_username', 'Admin'),
                reason=reason
            )
            db.session.add(movement)
            db.session.commit()
            
            flash(f'Asset moved successfully!', 'success')
            return redirect(url_for('admin_estate_assets'))
    
    locations = SchoolLocation.query.all()
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
            maintenance = SchoolMaintenanceRequest(
                asset_id=asset_id if asset_id else None,
                location_id=location_id if location_id else None,
                issue_description=issue_description,
                priority=priority,
                estimated_cost=estimated_cost,
                reported_by=session.get('admin_username', 'Admin'),
                notes=notes
            )
            db.session.add(maintenance)
            db.session.commit()
            
            flash('Maintenance request logged successfully!', 'success')
            return redirect(url_for('admin_estate_maintenance'))
    
    status_filter = request.args.get('status', 'all')
    query = SchoolMaintenanceRequest.query
    
    if status_filter != 'all':
        query = query.filter_by(status=status_filter)
    
    requests_list = query.order_by(
        SchoolMaintenanceRequest.priority.desc(),
        SchoolMaintenanceRequest.created_at.desc()
    ).all()
    
    assets = SchoolAsset.query.all()
    locations = SchoolLocation.query.all()
    
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
    
    maintenance = MaintenanceRequest.query.get_or_404(request_id)
    
    actual_cost = float(request.form.get('actual_cost', 0))
    contractor = request.form.get('contractor', '')
    notes = request.form.get('notes', '')
    
    maintenance.status = 'Completed'
    maintenance.actual_cost = actual_cost
    maintenance.contractor = contractor
    maintenance.completed_date = datetime.now().date()
    maintenance.notes = notes
    
    db.session.commit()
    flash('Maintenance request marked as completed!', 'success')
    
    return redirect(url_for('admin_estate_maintenance'))

# --- Filtered Print/SMS Routes for Admin ---

@app.route('/admin/filtered_actions', methods=['GET', 'POST'])
def admin_filtered_actions():
    """Admin page to filter students by department, year, semester for printing or SMS."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    filtered_students = []
    selected_department = request.args.get('department', '')
    selected_year = request.args.get('year', '')
    selected_semester = request.args.get('semester', '')
    
    if selected_department or selected_year or selected_semester:
        df = load_results_from_sheet()
        if not df.empty:
            # Filter by department
            if selected_department and selected_department != 'all':
                dept_col = COLUMN_MAPPING.get('Student Department')
                if dept_col and dept_col in df.columns:
                    df = df[df[dept_col].astype(str).str.lower() == selected_department.lower()]
            
            # Prepare filtered student list
            for index, row in df.iterrows():
                student_id = row.get(COLUMN_MAPPING.get('Student ID'), 'N/A')
                
                # Check if student has data for the selected semester
                if selected_year and selected_semester:
                    semester_key = f"{selected_year} - {selected_semester}"
                    student_full_data = get_all_student_results_by_id(str(student_id))
                    if semester_key not in student_full_data.get('results_by_semester', {}):
                        continue  # Skip students without data for this semester
                
                filtered_students.append({
                    'Student ID': student_id,
                    'Student Name': row.get(COLUMN_MAPPING.get('Student Name'), 'N/A'),
                    'Parent Phone': row.get(COLUMN_MAPPING.get('Parent Phone'), 'N/A'),
                    'Student Department': row.get(COLUMN_MAPPING.get('Student Department'), 'N/A')
                })
    
    return render_template('admin_filtered_actions.html',
                           filtered_students=filtered_students,
                           available_departments=['all'] + AVAILABLE_DEPARTMENTS,
                           available_years=AVAILABLE_YEARS,
                           available_semesters=AVAILABLE_GENERIC_SEMESTER_TYPES,
                           selected_department=selected_department,
                           selected_year=selected_year,
                           selected_semester=selected_semester,
                           student_count=len(filtered_students))


@app.route('/admin/send_filtered_sms', methods=['POST'])
def send_filtered_sms():
    """Send SMS to filtered students by department, year, semester."""
    if not session.get('admin_logged_in'):
        flash('Please log in to perform this action.', 'warning')
        return redirect(url_for('admin_login'))
    
    selected_department = request.form.get('department', '')
    selected_year = request.form.get('year', '')
    selected_semester = request.form.get('semester', '')
    
    if not selected_year or not selected_semester:
        flash('Please select both year and semester to send SMS.', 'warning')
        return redirect(url_for('admin_filtered_actions'))
    
    semester_key = f"{selected_year} - {selected_semester}"
    
    df = load_results_from_sheet()
    if df.empty:
        flash("Error loading student data.", 'danger')
        return redirect(url_for('admin_filtered_actions'))
    
    # Filter by department
    if selected_department and selected_department != 'all':
        dept_col = COLUMN_MAPPING.get('Student Department')
        if dept_col and dept_col in df.columns:
            df = df[df[dept_col].astype(str).str.lower() == selected_department.lower()]
    
    sent_count = 0
    failed_count = 0
    
    for index, row in df.iterrows():
        student_id = str(row.get(COLUMN_MAPPING.get('Student ID'), 'N/A'))
        student_name = row.get(COLUMN_MAPPING.get('Student Name'), 'N/A')
        phone_number = row.get(COLUMN_MAPPING.get('Parent Phone'), 'N/A')
        student_department = row.get(COLUMN_MAPPING.get('Student Department'), 'N/A')
        
        if not phone_number or phone_number == 'N/A':
            failed_count += 1
            continue
        
        # Get student results for the specific semester
        student_full_data = get_all_student_results_by_id(student_id)
        results_by_semester = student_full_data.get('results_by_semester', {})
        
        if semester_key not in results_by_semester:
            failed_count += 1
            continue
        
        semester_data = results_by_semester[semester_key]
        
        # Build SMS message
        message_lines = [f"Dear Parent of {student_name},"]
        if student_department != 'N/A':
            message_lines.append(f"Dept: {student_department}")
        message_lines.append(f"Results for {semester_key}:")
        
        all_subjects = []
        if semester_data.get('Core Subjects'):
            all_subjects.extend(semester_data['Core Subjects'])
        if semester_data.get('Elective Subjects'):
            all_subjects.extend(semester_data['Elective Subjects'])
        
        for subject_data in all_subjects:
            subject = subject_data.get('Subject', 'N/A')
            total_score = subject_data.get('Total Score', 'N/A')
            grade = subject_data.get('Grade', 'N/A')
            message_lines.append(f" {subject}: Tot={total_score}, Grd={grade}")
        
        message_lines.append(f"\nView full results: {WEBSITE_DOMAIN}{url_for('student_login')}")
        
        sms_content = "\n".join(message_lines)
        
        send_success, send_message = send_sms(phone_number, sms_content)
        if send_success:
            sent_count += 1
        else:
            failed_count += 1
            print(f"Failed SMS to {student_name}: {send_message}")
    
    flash(f"SMS sent to {sent_count} parents. Failed: {failed_count}.", 'info')
    return redirect(url_for('admin_filtered_actions', 
                           department=selected_department,
                           year=selected_year,
                           semester=selected_semester))


@app.route('/admin/print_filtered_results')
def print_filtered_results():
    """Generate printable results for filtered students."""
    if not session.get('admin_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('admin_login'))
    
    selected_department = request.args.get('department', '')
    selected_year = request.args.get('year', '')
    selected_semester = request.args.get('semester', '')
    
    if not selected_year or not selected_semester:
        flash('Please select both year and semester to print results.', 'warning')
        return redirect(url_for('admin_filtered_actions'))
    
    semester_key = f"{selected_year} - {selected_semester}"
    
    df = load_results_from_sheet()
    if df.empty:
        flash("Error loading student data.", 'danger')
        return redirect(url_for('admin_filtered_actions'))
    
    # Filter by department
    if selected_department and selected_department != 'all':
        dept_col = COLUMN_MAPPING.get('Student Department')
        if dept_col and dept_col in df.columns:
            df = df[df[dept_col].astype(str).str.lower() == selected_department.lower()]
    
    all_students_results = []
    
    for index, row in df.iterrows():
        student_id = str(row.get(COLUMN_MAPPING.get('Student ID'), 'N/A'))
        student_full_data = get_all_student_results_by_id(student_id)
        
        if semester_key in student_full_data.get('results_by_semester', {}):
            all_students_results.append({
                'student_info': student_full_data['student_info'],
                'semester_key': semester_key,
                'semester_data': student_full_data['results_by_semester'][semester_key]
            })
    
    return render_template('print_filtered_results.html',
                           all_students_results=all_students_results,
                           semester_key=semester_key,
                           selected_department=selected_department,
                           print_date=datetime.now().strftime('%Y-%m-%d %H:%M'))

# Initialize database tables
with app.app_context():
    try:
        # Test database connection
        connection = db.engine.connect()
        print("Successfully connected to PostgreSQL database!")
        connection.close()
        
        # Create all tables defined in models
        db.create_all()
        print("Database tables created successfully!")
        print(f"Connected to database: bisinessdb")
    except Exception as e:
        print(f"Error connecting to database: {e}")
        print("Please check your PostgreSQL connection string and ensure the database is accessible.")


if __name__ == '__main__':
    # In a production environment, use a production-ready WSGI server like Gunicorn or uWSGI
    # For local development, this is fine
    # Set debug=False for production
    app.run(debug=True)
