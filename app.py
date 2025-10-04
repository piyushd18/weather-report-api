from flask import Flask, request, jsonify, send_file
import requests
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
from io import BytesIO
import os
import tempfile
from dateutil import parser
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.backends.backend_pdf import PdfPages
import matplotlib.patches as mpatches

app = Flask(__name__)

# Database setup
def init_db():
    """Initialize SQLite database with weather data table"""
    conn = sqlite3.connect('weather_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS weather_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            temperature_2m REAL,
            relative_humidity_2m REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def fetch_weather_data(latitude, longitude):
    """Fetch weather data from Open-Meteo API for the past 2 days"""
    # Calculate date range for past 2 days
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=2)
    
    # Open-Meteo API URL - using standard forecast endpoint
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        'latitude': latitude,
        'longitude': longitude,
        'hourly': 'temperature_2m,relative_humidity_2m',
        'start_date': start_date.strftime('%Y-%m-%d'),
        'end_date': end_date.strftime('%Y-%m-%d'),
        'timezone': 'auto'
    }
    
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise Exception(f"Error fetching weather data: {str(e)}")

def store_weather_data(data, latitude, longitude):
    """Store weather data in SQLite database"""
    conn = sqlite3.connect('weather_data.db')
    cursor = conn.cursor()
    
    # Clear existing data for this location
    cursor.execute('DELETE FROM weather_data WHERE latitude = ? AND longitude = ?', 
                   (latitude, longitude))
    
    # Insert new data
    hourly_data = data.get('hourly', {})
    timestamps = hourly_data.get('time', [])
    temperatures = hourly_data.get('temperature_2m', [])
    humidity = hourly_data.get('relative_humidity_2m', [])
    
    for i, timestamp in enumerate(timestamps):
        temp = temperatures[i] if i < len(temperatures) else None
        humid = humidity[i] if i < len(humidity) else None
        
        cursor.execute('''
            INSERT INTO weather_data (timestamp, latitude, longitude, temperature_2m, relative_humidity_2m)
            VALUES (?, ?, ?, ?, ?)
        ''', (timestamp, latitude, longitude, temp, humid))
    
    conn.commit()
    conn.close()

@app.route('/weather-report')
def weather_report():
    """Fetch weather data and store in database"""
    try:
        # Get parameters from query string
        lat = request.args.get('lat', type=float)
        lon = request.args.get('lon', type=float)
        
        if lat is None or lon is None:
            return jsonify({'error': 'Missing required parameters: lat and lon'}), 400
        
        # Validate latitude and longitude ranges
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            return jsonify({'error': 'Invalid latitude or longitude values'}), 400
        
        # Fetch data from Open-Meteo API
        weather_data = fetch_weather_data(lat, lon)
        
        # Store data in database
        store_weather_data(weather_data, lat, lon)
        
        # Return success response with summary
        hourly_data = weather_data.get('hourly', {})
        data_points = len(hourly_data.get('time', []))
        
        return jsonify({
            'message': 'Weather data fetched and stored successfully',
            'location': {'latitude': lat, 'longitude': lon},
            'data_points': data_points,
            'date_range': {
                'start': hourly_data.get('time', [None])[0],
                'end': hourly_data.get('time', [None])[-1] if hourly_data.get('time') else None
            }
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/export/excel')
def export_excel():
    """Export last 48 hours of weather data to Excel file"""
    try:
        # Get the last 48 hours of data
        conn = sqlite3.connect('weather_data.db')
        
        # Calculate cutoff time (48 hours ago)
        cutoff_time = datetime.now() - timedelta(hours=48)
        
        query = '''
            SELECT timestamp, temperature_2m, relative_humidity_2m, latitude, longitude
            FROM weather_data 
            WHERE datetime(timestamp) >= datetime(?)
            ORDER BY timestamp
        '''
        
        df = pd.read_sql_query(query, conn, params=(cutoff_time.isoformat(),))
        conn.close()
        
        if df.empty:
            return jsonify({'error': 'No data available for the last 48 hours'}), 404
        
        # Create Excel file in memory
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Weather Data', index=False)
        
        output.seek(0)
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='weather_data.xlsx'
        )
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/export/pdf')
def export_pdf():
    """Generate PDF report with weather data chart using matplotlib"""
    try:
        # Get the last 48 hours of data
        conn = sqlite3.connect('weather_data.db')
        
        cutoff_time = datetime.now() - timedelta(hours=48)
        
        query = '''
            SELECT timestamp, temperature_2m, relative_humidity_2m, latitude, longitude
            FROM weather_data 
            WHERE datetime(timestamp) >= datetime(?)
            ORDER BY timestamp
        '''
        
        df = pd.read_sql_query(query, conn, params=(cutoff_time.isoformat(),))
        conn.close()
        
        if df.empty:
            return jsonify({'error': 'No data available for the last 48 hours'}), 404
        
        # Parse timestamps
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        # Create PDF with matplotlib
        pdf_output = BytesIO()
        
        with PdfPages(pdf_output) as pdf:
            # Create figure with multiple subplots
            fig = plt.figure(figsize=(12, 16))
            
            # Title page
            fig.suptitle('Weather Data Report', fontsize=20, fontweight='bold', y=0.95)
            
            # Metadata section
            ax_meta = plt.subplot(4, 1, 1)
            ax_meta.axis('off')
            
            location_info = f"Location: Lat {df['latitude'].iloc[0]:.2f}°, Lon {df['longitude'].iloc[0]:.2f}°"
            date_range = f"Date Range: {df['timestamp'].min().strftime('%Y-%m-%d %H:%M')} to {df['timestamp'].max().strftime('%Y-%m-%d %H:%M')}"
            data_points = f"Data Points: {len(df)} hourly measurements"
            generated = f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            
            metadata_text = f"{location_info}\n{date_range}\n{data_points}\n{generated}"
            ax_meta.text(0.1, 0.5, metadata_text, fontsize=12, verticalalignment='center',
                        bbox=dict(boxstyle="round,pad=0.5", facecolor="lightgray", alpha=0.8))
            
            # Temperature chart
            ax1 = plt.subplot(4, 1, 2)
            ax1.plot(df['timestamp'], df['temperature_2m'], 'r-', linewidth=2, label='Temperature (°C)')
            ax1.set_ylabel('Temperature (°C)', color='red', fontweight='bold')
            ax1.tick_params(axis='y', labelcolor='red')
            ax1.grid(True, alpha=0.3)
            ax1.set_title('Temperature Trend - Last 48 Hours', fontweight='bold')
            
            # Format x-axis for temperature
            ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
            ax1.xaxis.set_major_locator(mdates.HourLocator(interval=6))
            plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45)
            
            # Humidity chart
            ax2 = plt.subplot(4, 1, 3)
            ax2.plot(df['timestamp'], df['relative_humidity_2m'], 'b-', linewidth=2, label='Humidity (%)')
            ax2.set_ylabel('Relative Humidity (%)', color='blue', fontweight='bold')
            ax2.tick_params(axis='y', labelcolor='blue')
            ax2.grid(True, alpha=0.3)
            ax2.set_title('Humidity Trend - Last 48 Hours', fontweight='bold')
            ax2.set_xlabel('Time', fontweight='bold')
            
            # Format x-axis for humidity
            ax2.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
            ax2.xaxis.set_major_locator(mdates.HourLocator(interval=6))
            plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45)
            
            # Statistics table
            ax3 = plt.subplot(4, 1, 4)
            ax3.axis('off')
            
            # Create statistics
            stats_data = [
                ['Metric', 'Temperature (°C)', 'Humidity (%)'],
                ['Average', f"{df['temperature_2m'].mean():.1f}", f"{df['relative_humidity_2m'].mean():.1f}"],
                ['Maximum', f"{df['temperature_2m'].max():.1f}", f"{df['relative_humidity_2m'].max():.1f}"],
                ['Minimum', f"{df['temperature_2m'].min():.1f}", f"{df['relative_humidity_2m'].min():.1f}"],
                ['Range', f"{df['temperature_2m'].max() - df['temperature_2m'].min():.1f}", 
                 f"{df['relative_humidity_2m'].max() - df['relative_humidity_2m'].min():.1f}"]
            ]
            
            # Create table
            table = ax3.table(cellText=stats_data[1:], colLabels=stats_data[0], 
                             cellLoc='center', loc='center', bbox=[0.1, 0.3, 0.8, 0.4])
            table.auto_set_font_size(False)
            table.set_fontsize(10)
            table.scale(1, 2)
            
            # Style the table
            for i in range(len(stats_data[0])):
                table[(0, i)].set_facecolor('#4CAF50')
                table[(0, i)].set_text_props(weight='bold', color='white')
            
            ax3.set_title('Summary Statistics', fontweight='bold', pad=20)
            
            plt.tight_layout()
            pdf.savefig(fig, bbox_inches='tight')
            plt.close()
        
        pdf_output.seek(0)
        
        return send_file(
            pdf_output,
            mimetype='application/pdf',
            as_attachment=True,
            download_name='weather_report.pdf'
        )
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # Initialize database
    init_db()
    
    print("🚀 Starting server...")
    
    # Run the Flask app
    app.run(debug=True, host='0.0.0.0', port=5000)
