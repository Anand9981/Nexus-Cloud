import os
import re
import uuid
import boto3
import smtplib
import random
import certifi
import json
import traceback
from bson import json_util
from user_agents import parse 
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session
from dotenv import load_dotenv
from pymongo import MongoClient
from datetime import datetime, timedelta
from flask import make_response, redirect, url_for
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from bson.objectid import ObjectId
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from apscheduler.schedulers.background import BackgroundScheduler

# ---------------------------------------------------
# CONFIGURATION & CLOUD SETUP
# ---------------------------------------------------
load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'nexus_premium_key_999')

# AWS Configuration
ACCESS_KEY = os.getenv('AWS_ACCESS_KEY_ID')
SECRET_KEY_AWS = os.getenv('AWS_SECRET_ACCESS_KEY')
BUCKET_NAME = os.getenv('AWS_BUCKET_NAME')
REGION = os.getenv('AWS_REGION', 'us-east-1')

s3_client = boto3.client('s3', aws_access_key_id=ACCESS_KEY, aws_secret_access_key=SECRET_KEY_AWS, region_name=REGION)
rek_client = boto3.client('rekognition', aws_access_key_id=ACCESS_KEY, aws_secret_access_key=SECRET_KEY_AWS, region_name=REGION)

# Database Setup
MONGO_URI = os.getenv('MONGO_URI')
import certifi
client = MongoClient(
    MONGO_URI,
    tlsCAFile=certifi.where(),
    serverSelectionTimeoutMS=3000,
    connectTimeoutMS=3000,
    socketTimeoutMS=3000
)
# client = MongoClient(MONGO_URI, tlsCAFile=certifi.where(), serverSelectionTimeoutMS=5000)
db = client['NexusCloud_V2']
images_collection = db['assets']
users_collection = db['accounts']
folders_collection = db['directories']

# --- Yahan add karo ---
RECOVERY_OTP_CACHE = {} 
# ----------------------

# ---------------------------------------------------
# BACKGROUND CLEANUP SCHEDULER
# ---------------------------------------------------
def background_cleanup():
    """Daily automated task to process expired deletion requests."""
    print(f"[{datetime.utcnow()}] Running daily account cleanup task...")
    now = datetime.utcnow()
    
    # Un sabhi accounts ko dhundo jinhe 30 din ho chuke hain
    expired_accounts = list(users_collection.find({"is_scheduled_for_deletion": True, "deletion_scheduled_at": {"$lte": now}}))
    
    for user in expired_accounts:
        if user.get("delete_assets_option", False):
            # Asset cleanup logic (S3 + MongoDB)
            user_assets = list(images_collection.find({"uploader": user['username']}))
            for asset in user_assets:
                try:
                    s3_client.delete_object(Bucket=BUCKET_NAME, Key=asset['s3_key'])
                except Exception as e:
                    print(f"Error purging S3 asset: {e}")
            images_collection.delete_many({"uploader": user['username']})
        
        # User account delete karo
        users_collection.delete_one({"_id": user['_id']})
        print(f"Purged account: {user['username']}")

scheduler = BackgroundScheduler()
scheduler.add_job(func=background_cleanup, trigger="interval", days=1)
scheduler.start()

# ---------------------------------------------------
# AUTHENTICATION SETUP
# ---------------------------------------------------
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, user_data):
        self.id = str(user_data['_id'])
        self.username = user_data['username']
        self.email = user_data.get('email')
        self.profile_pic = user_data.get('profile_pic', 'https://ui-avatars.com/api/?name=' + user_data['username'])
        self.is_scheduled_for_deletion = user_data.get('is_scheduled_for_deletion', False)
        self.deletion_scheduled_at = user_data.get('deletion_scheduled_at')


@app.route('/test')
def test():
    return "Server is working perfectly!"


@login_manager.user_loader
def load_user(user_id):
    # Agar user_id invalid hua toh ObjectId error dega, isliye check zaroori hai
    try:
        user_data = users_collection.find_one({"_id": ObjectId(user_id)})
        return User(user_data) if user_data else None
    except:
        return None

# Smart Analytics Global Context Processor
@app.context_processor
def inject_usage_stats():
    if current_user.is_authenticated:
        total_assets = images_collection.count_documents({"uploader": current_user.username, "in_trash": False})
        trash_count = images_collection.count_documents({"uploader": current_user.username, "in_trash": True})
        return dict(total_assets=total_assets, trash_count=trash_count)
    return dict(total_assets=0, trash_count=0)

# ---------------------------------------------------
# CORE ROUTES (EXPLORE & SEARCH)
# ---------------------------------------------------

# @app.route('/')
# def index():
#     return render_template('index.html', images=[], folders=[], trending_tags=[], search_query='')

# ---------------------------------------------------
# CORE ROUTES (EXPLORE) - EXACT SAME FOR ALL USERS
# ---------------------------------------------------

@app.route('/')
def index():
    try:
        # 1. CORE PRIVACY (Sabke liye same feed - Login ho ya Non-Login)
        query = {
            "in_trash": {"$ne": True},
            "$or": [
                {"is_public": True},
                {"folder_name": {"$regex": "^General$", "$options": "i"}}
            ]
        }

        # 2. CONTENT FILTERING (Ye sirf Login user par apply hoga)
        if current_user.is_authenticated:
            user_profile = users_collection.find_one({"username": current_user.username})
            if user_profile and user_profile.get('blocked_tags'):
                blocked_tags = user_profile['blocked_tags']
                escaped = [re.escape(str(t).strip().lower()) for t in blocked_tags if str(t).strip()]
                if escaped:
                    query["$and"] = [
                        {"tags": {"$not": {"$elemMatch": {"$regex": "|".join(escaped), "$options": "i"}}}}
                    ]

        # 3. FETCH 50 IMAGES (Dono users ke liye common execution)
        pipeline = [
            {"$match": query},
            {"$sort": {"uploaded_at": -1}},  # Nayi photo sabse upar
            {"$limit": 50},                  # Sirf latest 50
            {"$lookup": {"from": "accounts", "localField": "uploader", "foreignField": "username", "as": "uploader_meta"}},
            {"$addFields": {"profile_pic": {"$arrayElemAt": ["$uploader_meta.profile_pic", 0]}}}
        ]
        
        all_images = list(images_collection.aggregate(pipeline))

        # Yahan se FOLDERS UI wala logic puri tarah hata diya gaya hai
        # Template ko seedha khali folders list [] bhej rahe hain
        return render_template('index.html', images=all_images, folders=[], trending_tags=[], search_query='')

    except Exception as e:
        print("CRITICAL ERROR IN ROUTE:", e)
        traceback.print_exc()
        return render_template('index.html', images=[], folders=[], trending_tags=[], search_query='')

# @app.route('/')
# def index():
#     try:
#         search_query = request.args.get('q', '').strip()
#         per_page = 30
        
#         # 1. Base Query Builder (Safely ignores missing in_trash fields)
#         query = {
#             "in_trash": {"$ne": True}, 
#             "$or": [
#                 {"is_public": True},
#                 {"folder_name": {"$regex": "^General$", "$options": "i"}}
#             ]
#         }
        
#         # 2. Search Query Logic (Regex Safe)
#         if search_query:
#             safe_query = re.escape(search_query)
#             query["$and"] = [{
#                 "$or": [
#                     {"tags": {"$regex": safe_query, "$options": "i"}},
#                     {"filename": {"$regex": safe_query, "$options": "i"}}
#                 ]
#             }]
            
#         # 3. Blocked Tags (User Preferences)
#         if current_user.is_authenticated:
#             user_profile = users_collection.find_one({"username": current_user.username})
#             if user_profile and user_profile.get('blocked_tags'):
#                 blocked_tags = user_profile['blocked_tags']
#                 escaped = [re.escape(str(t).strip().lower()) for t in blocked_tags if str(t).strip()]
#                 if escaped:
#                     block_condition = {"tags": {"$not": {"$elemMatch": {"$regex": "|".join(escaped), "$options": "i"}}}}
#                     # Safely append to $and query
#                     if "$and" in query:
#                         query["$and"].append(block_condition)
#                     else:
#                         query["$and"] = [block_condition]

#         # 4. Folders for the Dropdown UI (Safe Sorting)
#         user_folders = []
#         if current_user.is_authenticated:
#             user_folders = list(folders_collection.find({"owner": current_user.username}))
#             user_folders.sort(key=lambda x: str(x.get('_id')), reverse=True)
#             for folder in user_folders:
#                 folder['asset_count'] = images_collection.count_documents({
#                     "uploader": current_user.username, 
#                     "folder_name": folder['folder_name'],
#                     "in_trash": {"$ne": True}
#                 })

#         # 5. Trending Tags (Crash-Proof from Dirty Data)
#         trending = []
#         try:
#             trending = list(images_collection.aggregate([
#                 {"$match": query}, 
#                 {"$unwind": "$tags"}, 
#                 {"$group": {"_id": "$tags", "count": {"$sum": 1}}}, # Removed $toLower crash risk
#                 {"$sort": {"count": -1}}, 
#                 {"$limit": 10}
#             ]))
#             # Remove any empty/null tags that might have snuck in
#             trending = [t for t in trending if t.get('_id')]
#         except Exception as e:
#             print("Trending aggregation skipped due to bad data:", e)

#         # 6. Fetch Images
#         pipeline = [
#             {"$match": query},
#             {"$sort": {"uploaded_at": -1}}, 
#             {"$limit": per_page},
#             {"$lookup": {"from": "accounts", "localField": "uploader", "foreignField": "username", "as": "uploader_meta"}},
#             {"$addFields": {"profile_pic": {"$arrayElemAt": ["$uploader_meta.profile_pic", 0]}}}
#         ]
        
#         all_images = list(images_collection.aggregate(pipeline))
#         return render_template('index.html', images=all_images, folders=user_folders, trending_tags=trending, search_query=search_query)

#     except Exception as e:
#         # Ab backend silent fail nahi hoga, terminal me exact error dega
#         print("CRITICAL INDEX ERROR:", e)
#         traceback.print_exc()
#         return render_template('index.html', images=[], folders=[], trending_tags=[], search_query='')


# # ------------------------------------------------------------------
# # INFINITE SCROLL BACKEND (Same Crash-Proof Logic)
# # ------------------------------------------------------------------
# @app.route('/load-more')
# def load_more():
#     try:
#         scroll_page = request.args.get('page', 1, type=int)
#         search_query = request.args.get('q', '').strip()
#         skip_count = 30 + ((scroll_page - 1) * 20)
#         per_page = 20
        
#         query = {
#             "in_trash": {"$ne": True},
#             "$or": [
#                 {"is_public": True},
#                 {"folder_name": {"$regex": "^General$", "$options": "i"}}
#             ]
#         }
        
#         if search_query:
#             safe_query = re.escape(search_query)
#             query["$and"] = [{
#                 "$or": [
#                     {"tags": {"$regex": safe_query, "$options": "i"}},
#                     {"filename": {"$regex": safe_query, "$options": "i"}}
#                 ]
#             }]
            
#         if current_user.is_authenticated:
#             user_profile = users_collection.find_one({"username": current_user.username})
#             if user_profile and user_profile.get('blocked_tags'):
#                 blocked_tags = user_profile['blocked_tags']
#                 escaped = [re.escape(str(t).strip().lower()) for t in blocked_tags if str(t).strip()]
#                 if escaped:
#                     block_condition = {"tags": {"$not": {"$elemMatch": {"$regex": "|".join(escaped), "$options": "i"}}}}
#                     if "$and" in query:
#                         query["$and"].append(block_condition)
#                     else:
#                         query["$and"] = [block_condition]

#         pipeline = [
#             {"$match": query},
#             {"$sort": {"uploaded_at": -1}}, 
#             {"$skip": skip_count},          
#             {"$limit": per_page},           
#             {"$lookup": {"from": "accounts", "localField": "uploader", "foreignField": "username", "as": "uploader_meta"}},
#             {"$addFields": {"profile_pic": {"$arrayElemAt": ["$uploader_meta.profile_pic", 0]}}}
#         ]
        
#         new_images = list(images_collection.aggregate(pipeline))
#         return json.loads(json_util.dumps(new_images))
        
#     except Exception as e:
#         print("Infinite Scroll Error:", e)
#         return jsonify([])

# @app.route('/')
# def index():
#     try:
#         search_query = request.args.get('q', '').strip()
        
#         # DEFAULT: Shuru mein 30 images laayenge
#         per_page = 30 
        
#         query = {"in_trash": False}
        
#         if search_query:
#             query["$or"] = [
#                 {"tags": {"$regex": search_query, "$options": "i"}},
#                 {"filename": {"$regex": search_query, "$options": "i"}}
#             ]
#             query["is_public"] = True
#         else:
#             query["is_public"] = True
        
#         if current_user.is_authenticated:
#             user_profile = users_collection.find_one({"_id": ObjectId(current_user.id)})
#             blocked_tags = user_profile.get('blocked_tags', []) if user_profile else []
            
#             if blocked_tags:
#                 strict_filters = []
#                 for t in blocked_tags:
#                     clean_t = str(t).strip().lower()
#                     strict_filters.append(clean_t)
#                     strict_filters.append(f"#{clean_t}")
                
#                 regex_patterns = [f"^{re.escape(tag)}$" for tag in strict_filters]
#                 query["tags"] = {
#                     "$not": {
#                         "$elemMatch": {
#                             "$regex": "|".join(regex_patterns), 
#                             "$options": "i"
#                         }
#                     }
#                 }
            
#             if search_query:
#                 query["$and"] = [
#                     {"$or": [{"is_public": True}, {"uploader": current_user.username}]},
#                     {"$or": [
#                         {"tags": {"$regex": search_query, "$options": "i"}},
#                         {"filename": {"$regex": search_query, "$options": "i"}}
#                     ]}
#                 ]
#                 query.pop("is_public", None)
#                 query.pop("$or", None)
                
#             user_folders = list(folders_collection.find({"owner": current_user.username}))
#             for folder in user_folders:
#                 folder['asset_count'] = images_collection.count_documents({
#                     "uploader": current_user.username, 
#                     "folder_name": folder['folder_name'],
#                     "in_trash": False
#                 })
#         else:
#             user_folders = []
        
#         trending = list(images_collection.aggregate([
#             {"$match": query}, 
#             {"$unwind": "$tags"}, 
#             {"$group": {"_id": "$tags", "count": {"$sum": 1}}}, 
#             {"$sort": {"count": -1}}, 
#             {"$limit": 8}
#         ]))
        
#         pipeline = [
#             {"$match": query},
#             {"$sort": {"uploaded_at": -1}}, # NEWEST FIRST RULE
#             {"$limit": per_page},           # SIRF 30 IMAGES LOAD HOGI
#             {
#                 "$lookup": {
#                     "from": "accounts",
#                     "localField": "uploader",
#                     "foreignField": "username",
#                     "as": "uploader_meta"
#                 }
#             },
#             {
#                 "$addFields": {
#                     "profile_pic": {"$arrayElemAt": ["$uploader_meta.profile_pic", 0]}
#                 }
#             }
#         ]
        
#         all_images = list(images_collection.aggregate(pipeline))
#         return render_template('index.html', images=all_images, folders=user_folders, trending_tags=trending, search_query=search_query)
        
#     except Exception as e:
#         print(f"Explore Core Crisis: {str(e)}")
#         return render_template('index.html', images=[], folders=[], trending_tags=[], search_query='')

# # ------------------------------------------------------------------
# # 🔥 NAYA ROUTE: INFINITE SCROLL KE LIYE (Peeche se data layega)
# # ------------------------------------------------------------------
# @app.route('/load-more')
# def load_more():
#     try:
#         # Scroll count 1 se start hoga
#         scroll_page = request.args.get('page', 1, type=int)
#         search_query = request.args.get('q', '').strip()
        
#         # LOGIC: Pehle 30 already aachuke hain. Ab agle 20 lane hain.
#         # Agar scroll 1 hai: 30 skip karo, 20 lao. Agar scroll 2 hai: 50 skip karo, 20 lao.
#         skip_count = 30 + ((scroll_page - 1) * 20)
#         per_page = 20
        
#         # SAME FILTERS TAAKI SEARCH BHI SATH MEIN CHALE
#         query = {"in_trash": False}
#         if search_query:
#             query["$or"] = [{"tags": {"$regex": search_query, "$options": "i"}}, {"filename": {"$regex": search_query, "$options": "i"}}]
#             query["is_public"] = True
#         else:
#             query["is_public"] = True
            
#         if current_user.is_authenticated:
#             query["$or"] = [{"is_public": True}, {"uploader": current_user.username}]
#             query.pop("is_public", None)

#         pipeline = [
#             {"$match": query},
#             {"$sort": {"uploaded_at": -1}}, # Hamesha newest first
#             {"$skip": skip_count},          # Purani ignore karo
#             {"$limit": per_page},           # Nayi 20 uthao
#             {"$lookup": {"from": "accounts", "localField": "uploader", "foreignField": "username", "as": "uploader_meta"}},
#             {"$addFields": {"profile_pic": {"$arrayElemAt": ["$uploader_meta.profile_pic", 0]}}}
#         ]
        
#         new_images = list(images_collection.aggregate(pipeline))
        
#         # json_util se safe conversion hoti hai (_id string ban jayega)
#         return json.loads(json_util.dumps(new_images))
        
#     except Exception as e:
#         print("Infinite Scroll Failed:", str(e))
#         return jsonify([])

# @app.route('/')
# def index():
#     try:
#         search_query = request.args.get('q', '').strip()
#         query = {"in_trash": False}
        
#         if search_query:
#             query["$or"] = [
#                 {"tags": {"$regex": search_query, "$options": "i"}},
#                 {"filename": {"$regex": search_query, "$options": "i"}}
#             ]
#             query["is_public"] = True
#         else:
#             query["is_public"] = True
        
#         if current_user.is_authenticated:
#             user_profile = users_collection.find_one({"_id": ObjectId(current_user.id)})
#             blocked_tags = user_profile.get('blocked_tags', []) if user_profile else []
            
#             if blocked_tags:
#                 strict_filters = []
#                 for t in blocked_tags:
#                     clean_t = str(t).strip().lower()
#                     strict_filters.append(clean_t)
#                     strict_filters.append(f"#{clean_t}")
                
#                 regex_patterns = [f"^{re.escape(tag)}$" for tag in strict_filters]
#                 query["tags"] = {
#                     "$not": {
#                         "$elemMatch": {
#                             "$regex": "|".join(regex_patterns), 
#                             "$options": "i"
#                         }
#                     }
#                 }
            
#             if search_query:
#                 query["$and"] = [
#                     {"$or": [{"is_public": True}, {"uploader": current_user.username}]},
#                     {"$or": [
#                         {"tags": {"$regex": search_query, "$options": "i"}},
#                         {"filename": {"$regex": search_query, "$options": "i"}}
#                     ]}
#                 ]
#                 query.pop("is_public", None)
#                 query.pop("$or", None)
                
#             user_folders = list(folders_collection.find({"owner": current_user.username}))
#             for folder in user_folders:
#                 folder['asset_count'] = images_collection.count_documents({
#                     "uploader": current_user.username, 
#                     "folder_name": folder['folder_name'],
#                     "in_trash": False
#                 })
#         else:
#             user_folders = []
        
#         trending = list(images_collection.aggregate([
#             {"$match": query}, 
#             {"$unwind": "$tags"}, 
#             {"$group": {"_id": "$tags", "count": {"$sum": 1}}}, 
#             {"$sort": {"count": -1}}, 
#             {"$limit": 8}
#         ]))
        
#         pipeline = [
#             {"$match": query},
#             {"$sort": {"uploaded_at": -1}},
#             {
#                 "$lookup": {
#                     "from": "accounts",
#                     "localField": "uploader",
#                     "foreignField": "username",
#                     "as": "uploader_meta"
#                 }
#             },
#             {
#                 "$addFields": {
#                     "profile_pic": {"$arrayElemAt": ["$uploader_meta.profile_pic", 0]}
#                 }
#             }
#         ]
        
#         all_images = list(images_collection.aggregate(pipeline))
#         return render_template('index.html', images=all_images, folders=user_folders, trending_tags=trending, search_query=search_query)
        
#     except Exception as e:
#         print(f"Explore Core Aggregation dropout pipeline crisis: {str(e)}")
#         return render_template('index.html', images=[], folders=[], trending_tags=[], search_query='')

#---------------------------------------------------------------------------------
# General folder ke purane images ko public karo
#---------------------------------------------------------------------------------
# @app.route('/fix-old-data')
# def fix_old_data():
#     result = images_collection.update_many(
#         {"folder_name": {"$regex": "^General$", "$options": "i"}, "in_trash": False},
#         {"$set": {"is_public": True}}
#     )
#     return f"Fixed! {result.modified_count} images updated to public."

@app.route('/search')
def search():
    query = request.args.get('q')
    if not query: return redirect(url_for('index'))

    # 1. Search Filters Logic (अब Public OR General फोल्डर दोनों की इमेजेज सर्च होंगी)
    search_filter = {
        "in_trash": False,
        "$or": [
            {"is_public": True},          # कंडीशन 1: इमेज पब्लिक हो
            {"folder_name": "General"}   # कंडीशन 2: या फिर General फोल्डर की हो
        ],
        "$and": [
            {
                "$or": [
                    {"tags": {"$regex": query, "$options": "i"}},
                    {"filename": {"$regex": query, "$options": "i"}}
                ]
            }
        ]
    }
    
    if current_user.is_authenticated:
        user_profile = users_collection.find_one({"_id": ObjectId(current_user.id)})
        blocked_tags = user_profile.get('blocked_tags', []) if user_profile else []
        
        if blocked_tags:
            strict_filters = []
            for t in blocked_tags:
                clean_t = str(t).strip().lower()
                strict_filters.append(clean_t)
                strict_filters.append(f"#{clean_t}")
            regex_patterns = [f"^{re.escape(tag)}$" for tag in strict_filters]
            
            # Blocked tags को सर्च रिजल्ट्स से हटाना
            search_filter["tags"] = {
                "$not": {
                    "$elemMatch": {
                        "$regex": "|".join(regex_patterns), 
                        "$options": "i"
                    }
                }
            }

    # 2. Fetching Images
    results = list(images_collection.find(search_filter).sort("uploaded_at", -1))
    
    # 3. Fetching Folders for the Upload UI
    user_folders = []
    if current_user.is_authenticated:
        user_folders = list(folders_collection.find({"owner": current_user.username}).sort("_id", -1))
        
    # 4. Rendering Template
    return render_template('index.html', images=results, search_query=query, folders=user_folders)

@app.route('/increment-view/<img_id>', methods=['POST'])
@login_required
def increment_view(img_id):
    try:
        result = images_collection.find_one_and_update(
            {'_id': ObjectId(img_id)},
            {'$inc': {'views': 1}},
            return_document=True
        )
        if result:
            return jsonify({'status': 'success', 'new_views': result.get('views', 0)})
        return jsonify({'status': 'error', 'message': 'Asset missing'}), 404
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/user/<username>')
def uploader_profile_view(username):
    try:
        uploader_record = users_collection.find_one({"username": username})
        if not uploader_record:
            return render_template('404.html', text_override="The requested cloud identity profile perimeter does not exist within our database tracking cluster."), 404
            
        public_folders = list(folders_collection.find({
            "owner": username,
            "is_public": True
        }))
        
        for folder in public_folders:
            folder['asset_count'] = images_collection.count_documents({
                "uploader": username,
                "folder_name": folder['folder_name'],
                "in_trash": False,
                "is_public": True
            })
            
        public_images = list(images_collection.find({
            "uploader": username,
            "is_public": True,
            "in_trash": False
        }).sort("uploaded_at", -1))
        
        return render_template(
            'uploader_profile.html', 
            uploader=uploader_record, 
            folders=public_folders, 
            images=public_images
        )
        
    except Exception as e:
        print(f"Uploader Profile Context Processing Dropout: {str(e)}")
        return redirect(url_for('index'))

# ---------------------------------------------------
# ASSET MANAGEMENT (UPLOAD, FOLDERS & PRIVACY)
# ---------------------------------------------------

@app.route('/upload', methods=['POST'])
def upload():
    if 'image' not in request.files:
        return jsonify({"status": "error", "message": "Selection Required"}), 400

    files = request.files.getlist('image')
    selected_folder = request.form.get('folder_name', 'General')
    manual_tags = request.form.get('manual_tags', '').split(',')
    uploader = current_user.username if current_user.is_authenticated else "Guest"
    
    try:
        # --- FOLDER PRIVACY CHECK (Loop ke bahar taaki DB fast rahe) ---
        is_public_flag = False
        if selected_folder.lower() == 'general':
            is_public_flag = True  # General folder hamesha public
        elif current_user.is_authenticated:
            folder_doc = folders_collection.find_one({
                "folder_name": selected_folder, 
                "owner": current_user.username
            })
            # Agar folder public hai toh naya upload bhi public hoga
            is_public_flag = folder_doc.get('is_public', False) if folder_doc else False

        # --- PROCESS & UPLOAD FILES ---
        for file in files:
            if file and file.filename != '':
                orig_name = secure_filename(file.filename)
                filename = f"{datetime.now().timestamp()}_{orig_name}"
                
                # AWS S3 Upload
                s3_client.upload_fileobj(file, BUCKET_NAME, filename, ExtraArgs={'ContentType': file.content_type})
                
                # AWS Rekognition AI Tags
                rek_response = rek_client.detect_labels(Image={'S3Object': {'Bucket': BUCKET_NAME, 'Name': filename}}, MaxLabels=10)
                ai_tags = [label['Name'].lower() for label in rek_response['Labels']]
                
                final_tags = list(set(ai_tags + [t.strip().lower() for t in manual_tags if t.strip()]))
                file_url = f"https://{BUCKET_NAME}.s3.{REGION}.amazonaws.com/{filename}"
                
                # Save to Database
                images_collection.insert_one({
                    "filename": orig_name, 
                    "s3_key": filename, 
                    "url": file_url, 
                    "tags": final_tags,
                    "uploader": uploader, 
                    "folder_name": selected_folder,
                    "views": 0, 
                    "likes": 0, 
                    "shares": 0, 
                    "downloads": 0, 
                    "is_favorite": False,
                    "in_trash": False, 
                    "uploaded_at": datetime.utcnow(), 
                    "is_public": is_public_flag   # ← FIXED
                })

        return jsonify({"status": "success", "message": "Assets Synchronized Successfully"})
    
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/create-folder', methods=['POST'])
@login_required
def create_folder():
    folder_name = request.form.get('folder_name')
    if folder_name:
        folders_collection.insert_one({
            "folder_name": folder_name.strip(),
            "owner": current_user.username,
            "is_public": False,
            "created_at": datetime.utcnow()
        })
        return jsonify({"status": "success", "message": "Folder Created"})
    return jsonify({"status": "error", "message": "Invalid Name"})

@app.route('/folder/<name>')
@login_required
def folder_view(name):
    all_user_folders = list(folders_collection.find({"owner": current_user.username}))
    folder_images = list(images_collection.find({"uploader": current_user.username, "folder_name": name, "in_trash": False}).sort("uploaded_at", -1))
    return render_template('folder_view.html', folder_name=name, images=folder_images, all_user_folders=all_user_folders)

@app.route('/move-assets', methods=['POST'])
@login_required
def move_assets():
    try:
        data = request.get_json()
        asset_ids = data.get('asset_ids', [])
        target_folder = data.get('target_folder')
        
        if not asset_ids or not target_folder:
            return jsonify({'status': 'error', 'message': 'Invalid selection'})
            
        bson_ids = [ObjectId(id_str) for id_str in asset_ids]
        images_collection.update_many(
            {'_id': {'$in': bson_ids}, 'uploader': current_user.username},
            {'$set': {'folder_name': target_folder}}
        )
        return jsonify({'status': 'success', 'message': 'Assets moved successfully'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/rename-folder/<folder_id>', methods=['POST'])
@login_required
def rename_folder(folder_id):
    try:
        data = request.get_json() or {}
        new_name = data.get('new_name', '').strip()
        
        if not new_name:
            return jsonify({'status': 'error', 'message': 'Room name cannot be empty.'}), 400
            
        folders_collection.update_one(
            {'_id': ObjectId(folder_id), 'owner': current_user.username},
            {'$set': {'folder_name': new_name}}
        )
        
        images_collection.update_many(
            {'uploader': current_user.username, 'folder_name': folders_collection.find_one({'_id': ObjectId(folder_id)}).get('folder_name', '') if folders_collection.find_one({'_id': ObjectId(folder_id)}) else ''},
            {'$set': {'folder_name': new_name}}
        )
        
        return jsonify({'status': 'success', 'message': 'Folder renamed successfully.'})
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Internal re-indexing failure context: {str(e)}'}), 500

@app.route('/update-folder-privacy/<folder_id>', methods=['POST'])
@login_required
def update_folder_privacy(folder_id):
    data = request.get_json()
    is_public = data.get('is_public', False)
    
    folders_collection.update_one(
        {'_id': ObjectId(folder_id), 'owner': current_user.username},
        {'$set': {'is_public': is_public}}
    )
    
    folder = folders_collection.find_one({'_id': ObjectId(folder_id)})
    if folder:
        images_collection.update_many(
            {'folder_name': folder['folder_name'], 'uploader': current_user.username},
            {'$set': {'is_public': is_public}}
        )
    
    return jsonify({'status': 'success'})

@app.route('/delete-folder/<folder_id>', methods=['POST'])
@login_required
def delete_folder(folder_id):
    folder = folders_collection.find_one({'_id': ObjectId(folder_id), 'owner': current_user.username})
    if folder:
        images_collection.update_many(
            {'folder_name': folder['folder_name'], 'uploader': current_user.username},
            {'$set': {'in_trash': True, 'original_folder': folder['folder_name'], 'deleted_at': datetime.utcnow()}}
        )
        folders_collection.delete_one({'_id': ObjectId(folder_id)})
        return jsonify({'status': 'success'})
        
    return jsonify({'status': 'error', 'message': 'Folder not found'}), 404

@app.route('/toggle-folder-privacy/<folder_id>', methods=['POST'])
@login_required
def toggle_folder_privacy(folder_id):
    folder = folders_collection.find_one({"_id": ObjectId(folder_id), "owner": current_user.username})
    if folder:
        new_status = not folder.get('is_public', False)
        folders_collection.update_one({"_id": ObjectId(folder_id)}, {"$set": {"is_public": new_status}})
        images_collection.update_many(
            {"folder_name": folder['folder_name'], "uploader": current_user.username},
            {"$set": {"is_public": new_status}}
        )
        return jsonify({"status": "success", "new_status": "Public" if new_status else "Private"})
    return jsonify({"status": "error"}), 403

@app.route('/share-folder/<folder_name>')
def share_folder(folder_name):
    images = list(images_collection.find({"folder_name": folder_name, "is_public": True, "in_trash": False}))
    return render_template('index.html', images=images, folder_name=folder_name, is_shared_view=True)

# ---------------------------------------------------
# USER VAULT & PERSONAL FILES
# ---------------------------------------------------

@app.route('/my-vault')
@login_required
def my_vault():
    user_folders = list(folders_collection.find({'owner': current_user.username}))
    for folder in user_folders:
        count = images_collection.count_documents({
            'uploader': current_user.username, 
            'folder_name': folder['folder_name'],
            'in_trash': {'$ne': True}
        })
        folder['asset_count'] = count
        
    user_images = list(images_collection.find({"uploader": current_user.username, "in_trash": False}).sort("uploaded_at", -1))
    return render_template('vault.html', images=user_images, folders=user_folders)

# ---------------------------------------------------
# ENGAGEMENT & FAVORITES SYSTEM
# ---------------------------------------------------

@app.route('/favorites')
@login_required
def favorites():
    fav_images = list(images_collection.find({"uploader": current_user.username, "is_favorite": True, "in_trash": False}))
    return render_template('favorites.html', images=fav_images)

@app.route('/like-image/<image_id>', methods=['POST'])
def like_image(image_id):
    img = images_collection.find_one_and_update({"_id": ObjectId(image_id)}, {"$inc": {"likes": 1}}, return_document=True)
    if current_user.is_authenticated:
        images_collection.update_one({"_id": ObjectId(image_id)}, {"$set": {"is_favorite": True}})
    return jsonify({"status": "success", "new_likes": img.get('likes', 0)})

@app.route('/share-image/<image_id>', methods=['POST'])
def share_image(image_id):
    images_collection.update_one({"_id": ObjectId(image_id)}, {"$inc": {"shares": 1}})
    return jsonify({"status": "success"})

@app.route('/download-image/<image_id>')
def download_asset(image_id):
    asset = images_collection.find_one({"_id": ObjectId(image_id)})
    if asset:
        images_collection.update_one({"_id": ObjectId(image_id)}, {"$inc": {"views": 1, "downloads": 1}})
        return redirect(asset['url'])
    return "Asset not found", 404

# ---------------------------------------------------
# TRASH & BATCH BULK ROUTER STORAGE SYSTEM
# ---------------------------------------------------

@app.route('/bulk-trash-assets', methods=['POST'])
@login_required
def bulk_trash_assets():
    try:
        data = request.get_json() or {}
        asset_ids = data.get('asset_ids', [])
        if not asset_ids:
            return jsonify({'status': 'error', 'message': 'Payload structure contains no valid entities.'}), 400
            
        bson_ids_array = [ObjectId(id_str) for id_str in asset_ids]
        images_collection.update_many(
            {'_id': {'$in': bson_ids_array}, 'uploader': current_user.username},
            {'$set': {'in_trash': True, 'deleted_at': datetime.utcnow()}}
        )
        return jsonify({'status': 'success', 'message': 'Batch collection entity status rewritten successfully.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Internal collection stack anomaly context: {str(e)}'}), 500

@app.route('/move-to-trash/<image_id>', methods=['POST'])
@login_required
def move_to_trash(image_id):
    images_collection.update_one({"_id": ObjectId(image_id), "uploader": current_user.username}, {"$set": {"in_trash": True, "deleted_at": datetime.utcnow()}})
    return jsonify({"status": "success"})

@app.route('/restore-asset/<image_id>', methods=['POST'])
@login_required
def restore_asset(image_id):
    images_collection.update_one({"_id": ObjectId(image_id), "uploader": current_user.username}, {"$set": {"in_trash": False}, "$unset": {"deleted_at": ""}})
    return jsonify({"status": "success", "message": "Asset restored to vault"})

@app.route('/permanent-delete/<image_id>', methods=['POST'])
@login_required
def permanent_delete(image_id):
    asset = images_collection.find_one({"_id": ObjectId(image_id), "uploader": current_user.username})
    if asset:
        try:
            s3_client.delete_object(Bucket=BUCKET_NAME, Key=asset['s3_key'])
            images_collection.delete_one({"_id": ObjectId(image_id)})
            return jsonify({"status": "success", "message": "Asset purged permanently"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})
    return jsonify({"status": "error", "message": "Unauthorized"}), 403

@app.route('/empty-trash', methods=['POST'])
@login_required
def empty_trash():
    user_trash = list(images_collection.find({"uploader": current_user.username, "in_trash": True}))
    try:
        for asset in user_trash:
            s3_client.delete_object(Bucket=BUCKET_NAME, Key=asset['s3_key'])
        images_collection.delete_many({"uploader": current_user.username, "in_trash": True})
        return jsonify({"status": "success", "message": "Trash purged successfully"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/trash-bin')
@login_required
def trash_bin():
    expiry_limit = datetime.utcnow() - timedelta(days=30)
    images_collection.delete_many({"in_trash": True, "deleted_at": {"$lt": expiry_limit}})
    items = list(images_collection.find({"uploader": current_user.username, "in_trash": True}))
    return render_template('trash.html', items=items)

#---------------------------------------------------------------
# SECURITY CORE: ACCOUNT DELETION & RECOVERY
#---------------------------------------------------------------
@app.route('/request-account-deletion', methods=['POST'])
@login_required
def request_account_deletion():
    data = request.get_json() or {}
    delete_assets = data.get('delete_assets', False)
    
    # 1. ARCHIVE LOGIC: Agar assets preserve karne hain (User ne tick nahi kiya)
    if not delete_assets:
        images_collection.update_many(
            {"uploader": current_user.username}, 
            {"$set": {"status": "archived", "is_public": False}}
        )
    # Note: Agar delete_assets=True hai, toh background_cleanup 
    # 30 din baad script ke through S3 se delete kar dega.
    
    # 2. ACCOUNT LIFECYCLE: Deletion scheduling
    deletion_date = datetime.utcnow() + timedelta(days=30)
    
    users_collection.update_one(
        {"_id": ObjectId(current_user.id)},
        {"$set": {
            "is_scheduled_for_deletion": True,
            "delete_assets_option": delete_assets,
            "deletion_scheduled_at": deletion_date
        }}
    )
    
    # 3. SESSION SYNC: UI turant update karne ke liye
    session['is_scheduled_for_deletion'] = True
    session['deletion_scheduled_at'] = deletion_date.isoformat()
    
    return jsonify({
        'status': 'success', 
        'message': 'Account marked for deletion. Data lifecycle initiated.'
    })

#---------------------------------------------------------------------------------------
# ACCOUNT DELETION & RECOVERY ENDPOINTS
#---------------------------------------------------------------------------------------
@app.route('/cancel-account-deletion', methods=['POST'])
@login_required  # Security ke liye ye zaroori hai
def cancel_account_deletion():
    # Database se flags hatayein
    users_collection.update_one(
        {"_id": ObjectId(current_user.id)},
        {"$unset": {
            "is_scheduled_for_deletion": "", 
            "delete_assets_option": "", 
            "deletion_scheduled_at": ""
        }}
    )
    
    # Session se bhi flags hata dein taaki UI turant update ho
    session.pop('is_scheduled_for_deletion', None)
    session.pop('deletion_scheduled_at', None)
    
    return jsonify({'status': 'success'})

# ---------------------------------------------------
# SECURITY CORE: DYNAMIC ACCOUNT RECOVERY ENDPOINTS
# ---------------------------------------------------

def dispatch_smtp_secure_email(target_email, username, generated_otp):
    """Core SMTP utility mapping sequence to dispatch secure payload tokens"""
    sender_identity = "parmanandsahu2005@gmail.com"  
    smtp_app_secret = "naliuxxdahlxrkk"  
    
    msg = MIMEMultipart()
    msg['From'] = sender_identity
    msg['To'] = target_email
    msg['Subject'] = f"NEXUS Cloud Service - Secure Account Authentication Token"
    
    body_content = f"""
    Hello {username},
    
    An identity validation sequence was requested for your Nexus Cloud account.
    Please apply the following dynamic 6-digit security token within the parameter validation window:
    
    🔑 AUTHENTICATION OTP: {generated_otp}
    
    If you did not initiate this request, please log in immediately to modify your master encryption keys.
    
    Regards,
    Nexus Security Architecture Team
    """
    msg.attach(MIMEText(body_content, 'plain'))
    
    server = smtplib.SMTP('smtp.gmail.com', 587)
    server.starttls()
    server.login(sender_identity, smtp_app_secret)
    server.sendmail(sender_identity, target_email, msg.as_string())
    server.quit()

@app.route('/send-recovery-otp', methods=['POST'])
def send_recovery_otp():
    data = request.get_json() or {}
    username = data.get('username', '').strip()
    email = data.get('email', '').strip()
    
    user = users_collection.find_one({'username': username, 'email': email})
    if not user:
        return jsonify({'status': 'error', 'message': 'Account validation failed. Identity not registered.'}), 404
        
    generated_token = str(random.randint(100000, 999999))
    
    try:
        dispatch_smtp_secure_email(email, username, generated_token)
        # Note: RECOVERY_OTP_CACHE would need to be defined globally (e.g. RECOVERY_OTP_CACHE = {}) for this to persist across requests
        RECOVERY_OTP_CACHE[username] = generated_token
        return jsonify({'status': 'success', 'message': 'Payload routed successfully.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'SMTP transmission pipeline dropout: {str(e)}'}), 500

@app.route('/execute-secure-reset', methods=['POST'])
def execute_secure_reset():
    try:
        data = request.get_json() or {}
        
        username = data.get('username', '').strip()
        email = data.get('email', '').strip()
        input_question = data.get('security_question', '')
        input_answer = data.get('security_answer', '').strip().lower() 
        new_password = data.get('new_password', '')
        
        print(f"--- RESET DEBUG START ---")
        print(f"Form Username: '{username}', Email: '{email}'")
        print(f"Form Question: '{input_question}'")
        print(f"Form Answer (Lowered): '{input_answer}'")
        
        user = users_collection.find_one({'username': username, 'email': email})
        
        if not user:
            print("CRITICAL: User mapping not found in MongoDB Atlas! Triggering 404.")
            return render_template('404.html', text_override="The requested identity mapping profile has drifted beyond our cloud tracking perimeter context registry."), 404
            
        db_saved_question = user.get('security_question', '')
        db_saved_answer = str(user.get('security_answer', '')).strip().lower()
        
        print(f"DB Saved Question: '{db_saved_question}'")
        print(f"DB Saved Answer (Lowered): '{db_saved_answer}'")
        
        if input_question != db_saved_question or input_answer != db_saved_answer:
            print("SECURITY MISMATCH: Answer or Question did not match database nodes.")
            return jsonify({
                'status': 'error', 
                'message': 'Security secret answer verification rejected. Access update validation signature match failed.'
            }), 401
            
        new_hashed_signature = generate_password_hash(new_password)
        
        result = users_collection.update_one(
            {'_id': user['_id']},
            {'$set': {'password': new_hashed_signature}}
        )
        
        print(f"Database update acknowledged: {result.acknowledged}, Modified count: {result.modified_count}")
        print(f"--- RESET DEBUG END ---")
        
        return jsonify({'status': 'success', 'message': 'Master security authorization data metrics synchronized successfully.'})
        
    except Exception as e:
        print(f"EXCEPTION NODE FALLOUT: {str(e)}")
        return jsonify({'status': 'error', 'message': f'Internal verification stack matrix error node: {str(e)}'}), 500

@app.route('/internal-change-password', methods=['POST'])
@login_required
def internal_change_password():
    try:
        current_pw = request.form.get('current_password')
        new_pw = request.form.get('new_password')
        
        user_record = users_collection.find_one({'_id': ObjectId(current_user.id)})
        
        if user_record and check_password_hash(user_record['password'], current_pw):
            new_hashed_format = generate_password_hash(new_pw)
            
            users_collection.update_one(
                {'_id': ObjectId(current_user.id)},
                {'$set': {'password': new_hashed_format}}
            )
            return jsonify({'status': 'success', 'message': 'Master security credentials updated successfully.'})
        else:
            return jsonify({'status': 'error', 'message': 'The current password signature provided does not match.'}), 401
            
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Internal cluster operational dropout: {str(e)}'}), 500

# ---------------------------------------------------
# ACCREDITATION RECOVERY TERMINAL (RESET PASSWORD UI LINK)
# ---------------------------------------------------

@app.route('/reset-password', methods=['GET'])
def reset_password():
    """Renders the fresh dynamic account recovery interface template cleanly"""
    return render_template('reset_password.html')

@app.route('/secure-reset', methods=['POST'])
def secure_reset():
    return redirect(url_for('index'))

# ---------------------------------------------------
# COMPREHENSIVE USER CONFIGURATION (SETTINGS SYSTEM)
# ---------------------------------------------------

@app.route('/update-settings', methods=['POST'])
@login_required
def update_settings():
    avatar_choice = request.form.get('avatar_choice')
    
    if 'custom_profile_pic' in request.files:
        file = request.files['custom_profile_pic']
        if file and file.filename != '':
            try:
                orig_name = secure_filename(file.filename)
                unique_filename = f"profile_{current_user.username}_{int(datetime.now().timestamp())}_{orig_name}"
                
                s3_client.upload_fileobj(file, BUCKET_NAME, unique_filename, ExtraArgs={'ContentType': file.content_type})
                final_pic = f"https://{BUCKET_NAME}.s3.{REGION}.amazonaws.com/{unique_filename}"
                
                users_collection.update_one(
                    {"_id": ObjectId(current_user.id)}, 
                    {"$set": {"profile_pic": final_pic}}
                )
                
                flash("Profile system avatar synchronized from local system successfully!", "success")
                return redirect(url_for('settings'))
            except Exception as e:
                flash(f"Cloud synchronizer dropout: {str(e)}", "error")
                return redirect(url_for('settings'))

    if avatar_choice:
        users_collection.update_one(
            {"_id": ObjectId(current_user.id)}, 
            {"$set": {"profile_pic": avatar_choice}}
        )
        flash("AI system identity avatar registered successfully!", "success")
        
    return redirect(url_for('settings'))

@app.route('/update-profile', methods=['POST'])
@login_required
def update_profile():
    try:
        selected_avatar = request.form.get('selected_avatar')
        update_fields = {}
        
        if selected_avatar:
            update_fields['profile_pic'] = selected_avatar
            
        if 'custom_photo' in request.files:
            file = request.files['custom_photo']
            if file and file.filename != '':
                orig_name = secure_filename(file.filename)
                unique_filename = f"profile_{current_user.username}_{int(datetime.now().timestamp())}_{orig_name}"
                
                s3_client.upload_fileobj(file, BUCKET_NAME, unique_filename, ExtraArgs={'ContentType': file.content_type})
                
                final_pic = f"https://{BUCKET_NAME}.s3.{REGION}.amazonaws.com/{unique_filename}"
                update_fields['profile_pic'] = final_pic

        if update_fields:
            users_collection.update_one(
                {'_id': ObjectId(current_user.id)},
                {'$set': update_fields}
            )
            flash("Profile Identity parameters synchronized successfully!", "success")
            
        return redirect(url_for('settings'))
    except Exception as e:
        print(f"Profile Sync Exception: {str(e)}")
        return redirect(url_for('settings'))

@app.route('/synchronize-identity', methods=['POST'])
@login_required
def synchronize_identity():
    try:
        data = request.get_json()
        selected_avatar = data.get('selected_avatar')
        
        if not selected_avatar:
            return jsonify({'status': 'error', 'message': 'No avatar selected.'}), 400

        users_collection.update_one(
            {'_id': ObjectId(current_user.id)},
            {'$set': {'profile_pic': selected_avatar}}
        )
        
        # Session aur current_user ko update karna zaroori hai taki UI turant refresh ho
        current_user.profile_pic = selected_avatar
        session['profile_pic'] = selected_avatar
        session.modified = True
        
        return jsonify({'status': 'success', 'message': 'Profile Updated'})
    except Exception as e:
        print(f"DEBUG ERROR: {str(e)}") # Critical for finding the break
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/get-available-avatars', methods=['GET'])
@login_required
def get_available_avatars():
    """Core directory scanning mapping to locate verified asset strings layout grid"""
    avatar_dir = os.path.join(app.static_folder, 'images', 'avatars')
    
    if not os.path.exists(avatar_dir):
        return jsonify([])
        
    file_list = []
    for filename in os.listdir(avatar_dir):
        if filename.lower().endswith(('.png', '.jpg', '.jpeg')) and 'default' not in filename:
            file_list.append(filename)
            
    return jsonify(sorted(file_list))

@app.route('/block-tag', methods=['POST'])
@login_required
def block_tag():
    tag_to_block = request.form.get('tag_name', '').strip().lower()
    if tag_to_block:
        users_collection.update_one(
            {"_id": ObjectId(current_user.id)},
            {"$addToSet": {"blocked_tags": tag_to_block}}
        )
        flash(f"#{tag_to_block} successfully restricted from your content stream.", "success")
    return redirect(url_for('settings'))

@app.route('/unblock-tag/<tag_name>', methods=['POST'])
@login_required
def unblock_tag(tag_name):
    users_collection.update_one(
        {"_id": ObjectId(current_user.id)},
        {"$pull": {"blocked_tags": tag_name.lower()}}
    )
    flash(f"#{tag_name} restriction revoked successfully.", "success")
    return redirect(url_for('settings'))

# ---------------------------------------------------
# AUTHENTICATION
# ---------------------------------------------------

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        
        sec_question = request.form.get('security_question')
        sec_answer = request.form.get('security_answer', '').strip().lower()
        
        if not username or not email or not password:
            return jsonify({'status': 'error', 'message': 'All authorization parameters are required.'})
        
        try:
            if users_collection.find_one({"$or": [{"email": email}, {"username": username}]}):
                return jsonify({'status': 'error', 'message': 'Username or Email already exists.'})
                
            hashed_password = generate_password_hash(password)
            
            users_collection.insert_one({
                "username": username, 
                "email": email, 
                "password": hashed_password,
                "profile_pic": f"https://ui-avatars.com/api/?name={username}&background=2563eb&color=fff",
                "created_at": datetime.utcnow(),
                "blocked_tags": [],
                "security_question": sec_question,
                "security_answer": sec_answer
            })
            
            return jsonify({'status': 'success'})
            
        except Exception as database_error:
            print(f"MongoDB write transaction fallout registry error: {str(database_error)}")
            return jsonify({'status': 'error', 'message': 'Internal Cluster Registry Failure.'}), 500
            
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        input_username = request.form.get('username', '').strip()
        input_password = request.form.get('password', '')
        
        user_data = users_collection.find_one({"username": input_username})
        
        # 🛡️ CASE 1: UNIVERSAL IDENTITY INVALID
        if not user_data:
            return render_template('401.html', text_override="Requested profile ID is invalid. Please verify your identity name and try again."), 401
            
        # 🛡️ CASE 2: IDENTITY IS VALID BUT PASSWORD SIGNATURE REJECTED
        if not check_password_hash(user_data['password'], input_password):
            return jsonify({
                'status': 'password_error', 
                'message': 'Incorrect password signature. Please try again.'
            })
            
        # 🟢 CASE 3: ALL SIGNATURES REGISTERED SUCCESSFULLY
        login_user(User(user_data))
        
        # 1. Generate Session Token for device tracking
        session_token = str(uuid.uuid4())
        
        # 2. Store in Database session nodes
        ua = parse(request.user_agent.string)
        device_info = f"{ua.browser.family} on {ua.os.family}" # Result: "Chrome on Windows 11"
        
        session_data = {
            "user_id": user_data['_id'],
            "session_token": session_token,
            "device_info": request.user_agent.string,
            "ip_address": request.remote_addr,
            "last_active": datetime.utcnow()
        }
        db.sessions.insert_one(session_data)
        
        # 3. Construct Nexus Authenticated Response
        response = jsonify({
            'status': 'success', 
            'redirect_url': url_for('index'),
            'message': 'Master security authorization data metrics synchronized successfully.'
        })
        
        # login route में यहाँ बदलाव करें:
        response.set_cookie('nexus_session_token', session_token, httponly=True, secure=False)
        
        return response
        
    return render_template('login.html')

@app.route('/settings', methods=['GET'])
@login_required
def settings():
    user_data = users_collection.find_one({"_id": ObjectId(current_user.id)})
    # Fetch all sessions for this user
    user_sessions = list(db.sessions.find({"user_id": ObjectId(current_user.id)}))
    
    return render_template('settings.html', 
                           blocked_tags=user_data.get('blocked_tags', []),
                           user_sessions=user_sessions,
                           current_token=request.cookies.get('nexus_session_token'))

@app.before_request
def update_last_active():
    if current_user.is_authenticated:
        token = request.cookies.get('nexus_session_token')
        if token:
            db.sessions.update_one(
                {"session_token": token},
                {"$set": {"last_active": datetime.utcnow()}}
            )

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash("Signed out.", "success")
    return redirect(url_for('index'))

# @app.route('/debug-check')
# def debug_check():
#     try:
#         # Total images in DB
#         total = images_collection.count_documents({})
#         public = images_collection.count_documents({"is_public": True})
#         general = images_collection.count_documents({"folder_name": {"$regex": "^General$", "$options": "i"}})
#         with_tags = images_collection.count_documents({"tags": {"$exists": True, "$ne": []}})
        
#         # Sample image to see its structure
#         sample = images_collection.find_one({})
#         sample_info = {
#             "folder": sample.get("folder_name") if sample else None,
#             "is_public": sample.get("is_public") if sample else None,
#             "tags": sample.get("tags", [])[:5] if sample else None,
#             "uploader": sample.get("uploader") if sample else None
#         }
        
#         # Folders in DB
#         folders = []
#         if current_user.is_authenticated:
#             folders = list(folders_collection.find(
#                 {"owner": current_user.username}, 
#                 {"folder_name": 1, "_id": 0}
#             ))

#         return jsonify({
#             "total_images": total,
#             "public_images": public,
#             "general_folder_images": general,
#             "images_with_tags": with_tags,
#             "sample_image": sample_info,
#             "your_folders_in_db": folders,
#             "logged_in_as": current_user.username if current_user.is_authenticated else "NOT LOGGED IN"
#         })
#     except Exception as e:
#         return jsonify({"error": str(e)})

if __name__ == '__main__':
    # host ko '0.0.0.0' karna zaroori hai
    app.run(host='0.0.0.0', port=5000, debug=True)