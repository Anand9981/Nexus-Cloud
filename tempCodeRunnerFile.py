
    user_sessions = list(db.sessions.find({"user_id": ObjectId(current_user.id)}))
    
    return render_template('settings.html', 
                           blocked_tags=user_data.get('blocked_tags', []),
                           user_sessions=user_sessions,
                           current_token=request.cookies.get('nexus_session_token'))

@app.before_request
def update_last_active():