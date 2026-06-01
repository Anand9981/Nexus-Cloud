# ☁️ Nexus Cloud Gallery

Nexus Cloud Gallery is an AI-powered, secure, and scalable cloud-based image management system. Built with modern web technologies, it offers intelligent asset organization, automated AI tagging, and a premium Glassmorphism UI for a seamless user experience.

## ✨ Key Features

### 🧠 AI-Powered Intelligence
* [cite_start]**Automated Image Tagging:** Integrates directly with AWS Rekognition to automatically detect labels and generate searchable metadata for every uploaded image[cite: 18, 19].
* [cite_start]**Smart Search System:** Experience real-time gallery search functionality based on AI-generated tags and filename metadata[cite: 20, 21].
* [cite_start]**Content Filtering:** Features a custom "Blocked Tags" system, allowing users to automatically hide unwanted or restricted content categories from their gallery feed[cite: 22, 23].

### ☁️ Cloud & Asset Management
* [cite_start]**AWS S3 Integration:** Highly available, secure, and scalable cloud-based image storage powered by Amazon S3[cite: 3, 4].
* [cite_start]**Smart Organization:** Dynamic folder creation system (e.g., General, Work, Family) with automatic asset categorization for better content management[cite: 7, 8, 9, 10].
* [cite_start]**Batch Operations & Trash Bin:** Supports bulk selection for moving multiple assets between folders or transferring them to a temporary trash bin with an automated 30-day permanent deletion lifecycle[cite: 13, 14, 15, 16].

### 🛡️ Enterprise-Grade Security
* [cite_start]**Advanced Authentication:** Secure signup and login functionality with encrypted password hashing and CSRF protection using Flask-WTF[cite: 27, 28, 31, 32].
* [cite_start]**Multi-Layer Recovery:** Robust password reset workflow utilizing security questions and dynamic OTP verification via SMTP emails[cite: 29, 30].
* [cite_start]**Data Lifecycle Management:** Automated background maintenance jobs using APScheduler for scheduled account purges and expired asset cleanup[cite: 63, 64].
* [cite_start]**Secure Credential Handling:** All sensitive cloud credentials and database configurations are securely managed via environment variables[cite: 33, 34].

### 🎨 Premium UI/UX & Engagement
* [cite_start]**Glassmorphism Dashboard:** A modern, premium dashboard interface designed using Tailwind CSS, featuring transparency, blur effects, and cloud-inspired aesthetics[cite: 54, 55].
* [cite_start]**Dynamic Theming:** Integrated dark and light theme switching system with persistent user preferences using LocalStorage and Alpine.js[cite: 56, 57].
* [cite_start]**Social Engagement Metrics:** Interactive features including image likes, share functionality, real-time view tracking, and a dedicated Favorites bookmark system[cite: 43, 44, 49, 50].
* [cite_start]**Visual Previews:** Real-time upload preview matrix instantly displays images after upload initiation, supported by custom "Toast" animated notifications[cite: 11, 12, 51, 52].

## 💻 Tech Stack

* [cite_start]**Frontend:** Tailwind CSS, Alpine.js, HTML5, CSS3, JavaScript [cite: 72, 73]
* [cite_start]**Backend:** Python, Flask, APScheduler [cite: 74, 75]
* [cite_start]**Cloud & AI:** AWS S3, AWS Rekognition [cite: 76, 77]
* [cite_start]**Database & Security:** MongoDB, Flask-WTF, SMTP Authentication, python-dotenv [cite: 78, 79]


## ⚙️ Installation
To set up the project on your local machine, follow these steps:

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/Anand9981/NexusCloudGallery.git](https://github.com/Anand9981/NexusCloudGallery.git)
   cd NexusCloudGallery

2. Set up the virtual environment:

Bash
# Windows
python -m venv venv
venv\Scripts\activate

# Mac/Linux
python3 -m venv venv
source venv/bin/activate


3. Install dependencies:

Bash
pip install -r requirements.txt


4. Configure environment variables:
Create a .env file in the root directory and add your credentials:

Plaintext
SECRET_KEY=your_flask_secret_key
MONGO_URI=your_mongodb_uri
AWS_ACCESS_KEY_ID=your_aws_key
AWS_SECRET_ACCESS_KEY=your_aws_secret
AWS_BUCKET_NAME=your_aws_bucket_name
AWS_REGION=us-east-1


5. Run the application:

Bash
python main.py
The application will start running at http://127.0.0.1:5000.

🤝 Contribution
Contributions are always welcome. If you have suggestions or would like to report an issue, please feel free to open a pull request or contact me.

📝 License
This project is open-source and available under the MIT License.