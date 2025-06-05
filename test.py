from flask import Flask, render_template, request, redirect, url_for, flash, session, make_response, send_from_directory
import pandas as pd
import requests
import io
import urllib.parse # Import urllib.parse for URL encoding
from weasyprint import HTML # Import HTML from weasyprint
from werkzeug.security import generate_password_hash, check_password_hash # Import security utilities
from datetime import datetime # Import datetime for PDF timestamp
import os # Import os for file path operations
from werkzeug.utils import secure_filename # Import secure_filename for safe file uploads

app = Flask(__name__)
# --- Security Note: In a real application, use a strong, random secret key ---
# Secret key is needed for flashing messages.
app.secret_key = 'your_very_secret_key_replace_this' # Required for flashing messages and sessions

# --- Configuration ---
# Replace with your actual Google Sheet URL
GOOGLE_SHEET_URL = "https://docs.google.com/spreadsheets/d/168zURh3hUBgVmTq4dskZe3HQ9o_CagzHow6koVXdiZw/edit?gid=0#gid=0"
# Extract the sheet ID from the URL
SHEET_ID = GOOGLE_SHEET_URL.split('/d/')[1].split('/')[0]
# Construct the export URL for CSV. Assuming the sheet name is 'Sheet1'.
# If your sheet name is different, change 'sheet=Sheet1' accordingly.
CSV_EXPORT_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv&sheet=Sheet1"

# Arkesel API Configuration
# IMPORTANT: Double-check that this API key is correct and active in your Arkesel account.
# Using the API key provided by the user for the older endpoint.
ARKESEL_API_KEY = "b0FrYkNNVlZGSmdrendVT3hwUHk"
# Using the older GET-based SMS send URL provided by the user.
ARKESEL_SMS_URL = "https://sms.arkesel.com/sms/api"
# IMPORTANT: Replace with your registered Arkesel Sender ID.
# Verify this Sender ID is registered and approved in your Arkesel account.
ARKESEL_SENDER_ID = "GyedTuech" # e.g., "MySchool"

# --- Website Domain Configuration ---
# IMPORTANT: Replace with your actual website domain (e.g., 'https://your-school-website.com')
WEBSITE_DOMAIN = "http://127.0.0.1:5000" # Replace with your actual domain in production

# --- Admin Password Hashing ---
# Hash for the password 'gyedu2025'
# In a real application, generate this hash once and store it securely (e.g., in environment variables or a config file).
ADMIN_PASSWORD_HASH = generate_password_hash('gyedu2025') # Hashing the password 'gyedu2025'
print(f"Admin password hash (for 'gyedu2025'): {ADMIN_PASSWORD_HASH}") # Print hash for verification

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
    'fashion': ['Garment design', 'Garment construction', 'practicals'],
    'plumbing': ['Plumbing Principles', 'Plumbing Technology','practicals'],
    'Catering': ['Catering Principles', 'Catering Production','practicals'],
    'building': ['Building Principles', 'Building Technology','practicals'],
    'wood': ['Wood Principles', 'Wood Technology','practicals'],
}

# Combine all unique subject names for header parsing
ALL_GENERIC_SUBJECT_NAMES = sorted(list(set(CORE_SUBJECT_NAMES + [
    subject for sublist in ELECTIVE_SUBJECT_NAMES_BY_DEPARTMENT.values() for subject in sublist
])))

# Define the generic semester types used in your sheet (e.g., 'Semester 1', 'Sem 2').
# IMPORTANT: Add ALL your school's generic semester types here.
GENERIC_SEMESTER_TYPES = ['Semester 1', 'Semester 2'] # EXAMPLE: Add your actual semester types

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
    COLUMN_MAPPING, and FULL_SUBJECT_DETAILS_BY_SEMESTER_KEY by reading Google Sheet headers.
    """
    global AVAILABLE_YEARS, AVAILABLE_GENERIC_SEMESTER_TYPES, COLUMN_MAPPING, FULL_SUBJECT_DETAILS_BY_SEMESTER_KEY

    try:
        # Read only the header row from the CSV export URL
        df_headers = pd.read_csv(CSV_EXPORT_URL, nrows=0).columns.str.strip()
    except Exception as e:
        print(f"Error reading sheet headers for metadata initialization: {e}")
        # Initialize with empty lists/dicts if sheet cannot be read
        AVAILABLE_YEARS = []
        AVAILABLE_GENERIC_SEMESTER_TYPES = []
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
    
    # Update global variables
    AVAILABLE_YEARS = sorted(list(discovered_years))
    AVAILABLE_GENERIC_SEMESTER_TYPES = sorted(list(discovered_generic_semester_types))
    COLUMN_MAPPING.update(temp_column_mapping)
    FULL_SUBJECT_DETAILS_BY_SEMESTER_KEY = temp_full_subject_details

    print(f"Dynamically discovered years: {AVAILABLE_YEARS}")
    print(f"Dynamically discovered generic semester types: {AVAILABLE_GENERIC_SEMESTER_TYPES}")
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
    """Loads the student results from the Google Sheet into a pandas DataFrame."""
    try:
        # Read the CSV data directly from the export URL
        df = pd.read_csv(CSV_EXPORT_URL)
        # Optional: Clean up column names by removing leading/trailing spaces
        df.columns = df.columns.str.strip()

        # --- Verify essential columns exist immediately after loading ---
        # Use .get() for COLUMN_MAPPING keys as they are dynamically populated
        essential_cols = [COLUMN_MAPPING.get('Student ID'), COLUMN_MAPPING.get('Student Name'), COLUMN_MAPPING.get('Parent Phone')]
        if COLUMN_MAPPING.get('Student Department'): # Check if department column is mapped
            essential_cols.append(COLUMN_MAPPING.get('Student Department'))
        
        # Filter out None values from essential_cols if a mapping wasn't found
        essential_cols = [col for col in essential_cols if col is not None]

        if not all(col in df.columns for col in essential_cols):
            missing = [col for col in essential_cols if col not in df.columns]
            print(f"Error: Missing ESSENTIAL columns in sheet: {missing}")
            # Flash a message if possible, though this function might be called before request context is fully set up
            # flash(f"Error: Missing essential columns in sheet: {missing}", 'danger')
            return pd.DataFrame() # Return empty DataFrame if essential columns are missing
        # --- End essential column verification ---


        # Optional: Verify that all mapped columns exist (including subject details)
        # This check is now less critical as COLUMN_MAPPING is built from existing headers
        # but can still catch discrepancies if headers change after initial load.
        all_mapped_cols = list(COLUMN_MAPPING.values())
        missing_mapped = [col for col in all_mapped_cols if col not in df.columns]
        if missing_mapped:
            print(f"Warning: Missing some MAPPED columns in sheet: {missing_mapped}. Data for these columns will be 'N/A'.")


        # --- Fix for phone numbers ending in .0 ---
        # Ensure the Parent Phone column exists before attempting to clean it (already checked above, but defensive)
        if COLUMN_MAPPING.get('Parent Phone') and COLUMN_MAPPING['Parent Phone'] in df.columns:
            df[COLUMN_MAPPING['Parent Phone']] = df[COLUMN_MAPPING['Parent Phone']].astype(str).str.replace('.0', '', regex=False).str.strip()
            print("Cleaned Parent Phone column.")
        # --- End of fix ---


        print("Successfully loaded data from Google Sheet.")
        return df
    except Exception as e:
        print(f"Error loading data from Google Sheet: {e}")
        # flash(f"Error loading data from Google Sheet: {e}", 'danger') # Flash error if possible
        return pd.DataFrame() # Return empty DataFrame on error


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
            '2024 - Semester 1': {
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
            'Student Department': student_row.get(COLUMN_MAPPING.get('Student Department'), 'N/A')
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
        # Filter DataFrame by student name (case-insensitive)
        if COLUMN_MAPPING.get('Student Name') and COLUMN_MAPPING['Student Name'] in df.columns:
            df = df[df[COLUMN_MAPPING['Student Name']].astype(str).str.contains(search_query, case=False, na=False)]
        else:
            flash(f"Warning: Student Name column '{COLUMN_MAPPING.get('Student Name')}' not found for search.", 'warning')

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


@app.route('/student_result_pdf')
def student_result_pdf():
    """Generates a PDF report of a student's result using name and phone for verification."""
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


if __name__ == '__main__':
    # In a production environment, use a production-ready WSGI server like Gunicorn or uWSGI
    # For local development, this is fine
    # Set debug=False for production
    app.run(debug=True)
