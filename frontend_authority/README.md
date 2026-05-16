# Disaster Management Authority Dashboard

A modern, responsive web application for managing and responding to disaster alerts. Designed for emergency authorities like fireforce and police stations.

## Features

- 📊 **Real-time Disaster Monitoring**: View a live list of active disasters with severity levels
- 🚨 **Alert Broadcasting**: Send emergency notifications to all registered authorities with a single click
- 📍 **Location Tracking**: Displays geographic coordinates for each disaster
- 📈 **Statistics Dashboard**: Quick overview of total disasters by severity level
- 🎨 **Modern UI**: Clean, professional interface optimized for emergency response
- 📱 **Responsive Design**: Works seamlessly on desktop, tablet, and mobile devices
- 🔄 **API Integration**: Fetches real disasters from API endpoint (with mock data fallback)

## Setup Instructions

### 1. Installation

No build process required! This is a pure HTML/CSS/JavaScript application.

```bash
# Simply open the index.html file in a web browser
open index.html
# or
firefox index.html
# or serve via a local HTTP server
python -m http.server 8000
# Then visit http://localhost:8000
```

### 2. Configuration

#### API Endpoint
The dashboard is pre-configured to connect to `https://api.samstack.site` but you can change this:

1. Open the dashboard
2. In the "Controls" section at the top, modify the "API Endpoint" field with your server URL
3. Click "Refresh Disasters" to fetch data

#### Mock Data
If the API is unavailable or returns no data, the application automatically falls back to mock disaster data. This is useful for testing and demonstration purposes.

## API Integration

### Fetching Disasters

The application attempts to fetch disasters from:
```
GET {API_ENDPOINT}/db/rows?table=disasters
```

Expected response format:
```json
[
  {
    "id": 1,
    "title": "Wildfire - Forest Zone Alpha",
    "type": "Wildfire",
    "location": "North Forest District",
    "description": "Large wildfire spreading...",
    "severity": "critical",
    "timestamp": "2026-05-15T10:30:00Z",
    "latitude": 40.7128,
    "longitude": -74.0060,
    "affectedPeople": 500,
    "status": "Active"
  }
]
```

### Sending Disaster Alerts

When you click the "Notify All" button, the application sends:
```
POST {API_ENDPOINT}/api/alerts
```

Request body:
```json
{
  "Query": "Disaster Title",
  "Mode": "critical",
  "UserLat": 40.7128,
  "UserLng": -74.0060,
  "DisasterType": "Wildfire",
  "Location": "North Forest District",
  "Description": "Detailed description...",
  "AffectedPeople": 500
}
```

## How to Use

### 1. Load Disasters
- Open the dashboard
- Click the "Refresh Disasters" button to load the latest disasters from the API
- The system will display all disasters in card format

### 2. Understand Severity Levels
- **🔴 Critical**: Immediate threat requiring urgent response
- **🟠 Moderate**: Significant event requiring attention
- **🟢 Low**: Minor incident for monitoring

### 3. Send Alerts
1. Click the "📢 Notify All" button on any disaster card
2. Review the alert preview in the modal dialog
3. Click "Send Alert to All Authorities" to broadcast the alert
4. All registered authorities will receive push notifications

### 4. Monitor Statistics
- View the statistics cards at the bottom for quick overview:
  - Total Disasters
  - Critical Count
  - Moderate Count
  - Low Severity Count

## Features Explained

### Disaster Cards
Each card displays:
- **Title**: Short description of the disaster
- **Severity Badge**: Color-coded severity indicator
- **Time Ago**: When the disaster was reported
- **Full Description**: Detailed information
- **Type**: Category of disaster
- **Location**: Geographic location
- **Status**: Current status (Active, Monitoring, Warning, etc.)
- **Affected People**: Estimated number of people in affected area
- **Notify Button**: Click to send alert

### Modal Dialog
When sending an alert:
- Verify disaster information
- See a preview of the alert message
- Real alert title and description are shown
- Confirm before broadcasting

### Toast Notifications
Get instant feedback:
- Success messages (green)
- Error messages (red)
- Info messages (blue)

## Customization

### Change Colors
Edit `style.css` and modify the CSS variables or color codes:
- Header gradient: `background: linear-gradient(135deg, #e74c3c 0%, #c0392b 100%);`
- Primary color: `#667eea`
- Accent color: `#764ba2`

### Modify Mock Data
Edit the `mockDisasters` array in `script.js` to add/remove test disasters.

### Adjust Grid Layout
In `style.css`, modify the grid configuration:
```css
.disasters-grid {
    grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
}
```

## Troubleshooting

### Disasters Not Loading?
1. Check if the API endpoint is correct
2. Verify CORS is enabled on your backend
3. Check browser console for error messages (Press F12)
4. The app will automatically fall back to mock data

### Alert Not Sending?
1. Verify the API endpoint is correct
2. Check network tab in browser developer tools
3. Ensure backend is running and accessible
4. Check that `/api/alerts` endpoint is implemented

### CORS Issues?
If you see CORS errors:
1. Ensure your backend has CORS enabled (as shown in the C# code)
2. Backend already includes: `builder.Services.AddCors(...)`
3. Check that the backend is properly configured

## Browser Support

- Chrome/Chromium: ✅ Full support
- Firefox: ✅ Full support
- Safari: ✅ Full support
- Edge: ✅ Full support
- IE11: ❌ Not supported

## File Structure

```
frontend_authority/
├── index.html       # Main HTML file
├── style.css        # All styling and responsive design
├── script.js        # Application logic and API integration
└── README.md        # This file
```

## Security Notes

⚠️ **Development Notice**: This is a frontend dashboard for development purposes.

For production:
1. Implement authentication to verify authorized users only
2. Add rate limiting to prevent alert spam
3. Implement audit logging for all alert broadcasts
4. Use HTTPS for all API communications
5. Add role-based access control (RBAC)
6. Validate all input on both frontend and backend

## API Backend Reference

The application expects a C# ASP.NET backend with:
- `GET /db/rows?table=disasters` - Returns list of disasters
- `POST /api/alerts` - Broadcasts alert to all authorities
- CORS enabled for HTML5 cross-origin requests

Sample backend code is included in the project documentation.

## License

This project is part of the Hackathon initiative for emergency response systems.

## Support

For issues or questions:
1. Check the browser console (F12) for error messages
2. Verify API endpoint configuration
3. Ensure backend server is running
4. Check network requests in DevTools Network tab

---

**Last Updated**: May 15, 2026
**Version**: 1.0
