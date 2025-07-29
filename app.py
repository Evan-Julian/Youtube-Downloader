from urllib.parse import urlparse, parse_qs
from flask import Flask, render_template, request, send_file, redirect, url_for, session, jsonify, after_this_request
import os
import uuid
import re
import threading
from pytubefix import YouTube, Playlist
from moviepy.editor import VideoFileClip, AudioFileClip
import time
import zipfile

app = Flask(__name__)
app.secret_key = 'your_strong_random_secret_key_here' 

DOWNLOAD_FOLDER = "downloads"
TEMP_FOLDER = os.path.join(DOWNLOAD_FOLDER, "temp")
PLAYLIST_DOWNLOADS_FOLDER = os.path.join(DOWNLOAD_FOLDER, "playlists")

if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)
if not os.path.exists(TEMP_FOLDER):
    os.makedirs(TEMP_FOLDER)
if not os.path.exists(PLAYLIST_DOWNLOADS_FOLDER):
    os.makedirs(PLAYLIST_DOWNLOADS_FOLDER)

download_progress = {}

def clean_youtube_url(url):
    parsed = urlparse(url)
    youtube_video_domains = {'www.youtube.com', 'youtube.com', 'm.youtube.com', 'youtu.be'} 
    youtube_shorts_domains = {'youtube.com0'}
    
    if 'playlist?' in parsed.query or '/playlist?' in parsed.path or 'list=' in parsed.query:
        return url 

    if parsed.netloc not in youtube_video_domains and parsed.netloc not in youtube_shorts_domains:
        raise ValueError("URL yang dimasukkan bukan URL YouTube yang valid (domain tidak dikenal).")

    video_id = None
    if parsed.netloc in youtube_video_domains:
        query = parse_qs(parsed.query)
        video_id = query.get("v", [None])[0] 
    elif parsed.netloc in youtube_shorts_domains:
        path_segments = parsed.path.lstrip('/').split('/')
        if len(path_segments) > 1 and path_segments[0] == 'shorts' and path_segments[1]:
            video_id = path_segments[1]

    if not video_id:
        raise ValueError("URL tidak valid! Tidak ditemukan ID video YouTube.")
    
    return f"https://www.youtube.com/watch?v={video_id}"

def sanitize_filename(title):
    sanitized_title = re.sub(r'[\\/:*?"<>|]', '', title)
    sanitized_title = sanitized_title.replace(' ', '_')
    return sanitized_title[:100]

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/youtube_shorts')
def youtube_shorts_page():
    return render_template('youtube_shorts.html')

@app.route('/youtube_playlist')
def youtube_playlist_page():
    return render_template('youtube_playlist.html')

@app.route('/video_info', methods=['POST'])
def video_info():
    url = request.form['url']
    format_type = request.form['format']

    try:
        clean_url = clean_youtube_url(url)
        yt = YouTube(clean_url)

        resolutions = []
        if format_type == 'mp4':
            all_mp4_streams = yt.streams.filter(file_extension='mp4').order_by('resolution').desc()
            
            unique_resolutions = set()
            for stream in all_mp4_streams:
                if stream.resolution:
                    unique_resolutions.add(stream.resolution)
            
            resolutions = sorted(
                list(unique_resolutions),
                key=lambda x: int(x[:-1]) if x and x[-1] == 'p' else 0,
                reverse=True
            )

            if not resolutions:
                raise Exception("Tidak ada stream video MP4 yang tersedia untuk diunduh.")

        return render_template(
            'video_info.html',
            title=yt.title,
            thumbnail=yt.thumbnail_url,
            url=clean_url,
            format_type=format_type,
            resolutions=resolutions
        )
    except Exception as e:
        error_message = str(e)
        if "HTTP Error 400" in error_message or "KeyError" in error_message:
            error_message = f"💥 Gagal mendapatkan informasi video. Ini sering terjadi karena YouTube telah melakukan perubahan. Mohon coba update `pytubefix` Anda (`pip install --upgrade pytubefix`) dan coba lagi nanti. Detail: {str(e)}"
        elif "Age restricted" in error_message:
            error_message = "Video ini dibatasi usia atau tidak dapat diunduh."
        else:
            error_message = f"💥 Gagal ambil data video: {str(e)}"
        return render_template('error.html', message=error_message)

def _download_and_process_single_video(video_url, format_type, quality, download_id, default_title, default_thumbnail, base_download_folder, is_playlist_item=False, item_index=0, total_items=0, app_context=None):
    temp_files_for_this_item = []
    if app_context: 
        app.app_context().push()
    
    original_video_title = default_title
    original_thumbnail_url = default_thumbnail

    try:
        def on_progress_callback(stream, chunk, bytes_remaining):
            total_size = stream.filesize
            bytes_downloaded = total_size - bytes_remaining
            
            percentage = (bytes_downloaded / total_size) * 100
            if percentage > 99.9: 
                percentage = 100.0

            if download_id in download_progress:
                if is_playlist_item:
                    download_progress[download_id]['current_video_progress'] = percentage
                    download_progress[download_id]['message'] = f"Mengunduh ({item_index+1}/{total_items}): {original_video_title[:30]}... - {percentage:.0f}%"
                else:
                    download_progress[download_id]['progress'] = percentage
                    download_progress[download_id]['status'] = 'downloading'
                    download_progress[download_id]['message'] = f"Mengunduh {percentage:.0f}%"


        yt = YouTube(video_url, on_progress_callback=on_progress_callback)
        original_video_title = yt.title
        original_thumbnail_url = yt.thumbnail_url
        
        sanitized_title = sanitize_filename(original_video_title)
        final_filepath = None
        download_name = ""

        if format_type == 'mp4':
            # Prioritas 1: Cari stream progresif (video+audio) MP4 dengan kualitas yang diminta
            stream = yt.streams.filter(progressive=True, file_extension='mp4', res=quality).first()
            
            if stream: # Jika ditemukan stream progresif
                output_filename = f"{sanitized_title}_{quality}.mp4"
                final_filepath = os.path.join(base_download_folder, output_filename)
                stream.download(output_path=base_download_folder, filename=output_filename)
                download_name = f"{original_video_title}_{quality}.mp4"
            else: # Prioritas 2: Jika tidak ada stream progresif, cari video-only dan audio-only
                if is_playlist_item:
                    download_progress[download_id]['message'] = f"Memproses ({item_index+1}/{total_items}): Mengunduh video & audio {original_video_title[:30]}..."
                else:
                    download_progress[download_id]['message'] = 'Mengunduh video & audio terpisah...'

                # PERUBAHAN DI SINI: Logika pemilihan stream video adaptif (non-progresif)
                if quality == 'highest': # Jika kualitas tertinggi diminta
                    # Dapatkan stream video MP4 terbaik (resolusi tertinggi)
                    video_stream = yt.streams.filter(file_extension='mp4', type='video').order_by('resolution').desc().first()
                    if not video_stream: # Fallback ke webm jika tidak ada mp4 video-only
                        video_stream = yt.streams.filter(file_extension='webm', type='video').order_by('resolution').desc().first()
                else: # Jika kualitas spesifik (misal '144p', '720p', '1080p') diminta
                    video_stream = yt.streams.filter(res=quality, file_extension='mp4', type='video').first()
                    if not video_stream: # Fallback ke webm jika tidak ada mp4 video-only
                        video_stream = yt.streams.filter(res=quality, file_extension='webm', type='video').first()
                
                audio_stream = yt.streams.filter(only_audio=True).order_by('abr').desc().first() # Cari stream audio terbaik

                if not video_stream:
                    raise Exception(f"Stream video {quality} tidak tersedia untuk {original_video_title} (tidak progresif dan tidak adaptif yang cocok).")
                if not audio_stream:
                    raise Exception(f"Stream audio tidak tersedia untuk {original_video_title}.")
                
                video_temp_download_path = os.path.join(TEMP_FOLDER, f"{uuid.uuid4()}_video.{video_stream.mime_type.split('/')[-1]}")
                audio_temp_download_path = os.path.join(TEMP_FOLDER, f"{uuid.uuid4()}_audio.{audio_stream.mime_type.split('/')[-1]}")

                video_stream.download(output_path=TEMP_FOLDER, filename=os.path.basename(video_temp_download_path))
                audio_stream.download(output_path=TEMP_FOLDER, filename=os.path.basename(audio_temp_download_path))

                temp_files_for_this_item.append(video_temp_download_path)
                temp_files_for_this_item.append(audio_temp_download_path)

                if is_playlist_item:
                     download_progress[download_id]['message'] = f"Memproses ({item_index+1}/{total_items}): Menggabungkan {original_video_title[:30]}..."
                else:
                    download_progress[download_id]['message'] = 'Menggabungkan video & audio...'
                    download_progress[download_id]['progress'] = 90

                video_clip = VideoFileClip(video_temp_download_path)
                audio_clip = AudioFileClip(audio_temp_download_path)
                
                final_clip = video_clip.set_audio(audio_clip)

                output_filename_merged = f"{sanitized_title}_{quality}.mp4"
                final_filepath = os.path.join(base_download_folder, output_filename_merged)
                
                final_clip.write_videofile(final_filepath, codec='libx264', audio_codec='aac', logger=None)
                
                video_clip.close()
                audio_clip.close()
                final_clip.close()
                
                download_name = f"{original_video_title}_{quality}.mp4"
            
        elif format_type == 'mp3':
            stream = yt.streams.filter(only_audio=True).order_by('abr').desc().first()

            if stream is None:
                raise Exception(f"Stream audio tidak tersedia untuk {original_video_title}.")
            
            filename_base = f"{sanitized_title}"
            original_ext = stream.mime_type.split('/')[-1]
            
            temp_audio_download_path = os.path.join(TEMP_FOLDER, f"{uuid.uuid4()}_audio_raw.{original_ext}")
            
            if is_playlist_item:
                download_progress[download_id]['message'] = f"Memproses ({item_index+1}/{total_items}): Mengunduh audio {original_video_title[:30]}..."
            else:
                download_progress[download_id]['message'] = 'Mengunduh audio...'
            stream.download(output_path=TEMP_FOLDER, filename=os.path.basename(temp_audio_download_path))

            temp_files_for_this_item.append(temp_audio_download_path)

            if original_ext != 'mp3':
                if is_playlist_item:
                    download_progress[download_id]['message'] = f"Memproses ({item_index+1}/{total_items}): Mengonversi {original_video_title[:30]} ke MP3..."
                else:
                    download_progress[download_id]['message'] = 'Mengonversi audio ke MP3...'
                    download_progress[download_id]['progress'] = 90

                audio_clip = AudioFileClip(temp_audio_download_path)
                final_filename_mp3 = f"{filename_base}.mp3"
                final_filepath = os.path.join(base_download_folder, final_filename_mp3)
                
                audio_clip.write_audiofile(final_filepath, codec='libmp3lame', logger=None)
                audio_clip.close()
                
            else:
                final_filename_mp3 = f"{filename_base}.mp3"
                final_filepath = os.path.join(base_download_folder, final_filename_mp3)
                os.rename(temp_audio_download_path, final_filepath)
            
            download_name = f"{original_video_title}.mp3"

        else:
            raise Exception("Format tidak dikenali (hanya mp4 dan mp3).")

        if not is_playlist_item: # Hanya update status complete jika ini unduhan tunggal
            download_progress[download_id] = {
                'progress': 100,
                'status': 'complete',
                'filepath': final_filepath,
                'download_name': download_name,
                'title': original_video_title,
                'thumbnail': original_thumbnail_url,
                'original_url': video_url
            }
        
        return {
            'status': 'success',
            'filepath': final_filepath,
            'download_name': download_name,
            'title': original_video_title,
            'thumbnail': original_thumbnail_url,
            'original_url': video_url
        }

    except Exception as e:
        error_msg = str(e)
        if "HTTP Error 400" in error_msg or "KeyError" in error_msg:
            error_msg = "Gagal memproses video. YouTube mungkin telah melakukan perubahan."
        elif "Age restricted" in error_msg:
            error_msg = "Video dibatasi usia atau tidak dapat diunduh."
        elif "ffmpeg" in error_msg.lower():
            error_msg = f"Gagal memproses video/audio (ffmpeg error). Pastikan `ffmpeg` terinstal dan MoviePy dapat mengaksesnya."
        else:
            error_msg = f"Terjadi kesalahan tak terduga: {str(e)}"
        
        if download_id in download_progress:
            download_progress[download_id].update({
                'progress': 0,
                'status': 'error',
                'message': error_msg,
                'title': locals().get('original_video_title', default_title),
                'thumbnail': locals().get('original_thumbnail_url', default_thumbnail),
                'original_url': video_url
            })
        return {
            'status': 'error',
            'message': error_msg,
            'title': locals().get('original_video_title', default_title),
            'thumbnail': locals().get('original_thumbnail_url', default_thumbnail),
            'original_url': video_url
        }
    finally:
        for f in temp_files_for_this_item:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception as cleanup_error:
                print(f"Warning: Failed to clean up temp file {f}: {cleanup_error}")
        if app_context:
            app_context.pop()


@app.route('/download', methods=['POST'])
def download_video_route():
    url = request.form['url']
    format_type = request.form['format']
    quality = request.form.get('quality', 'highest')

    download_id = str(uuid.uuid4())
    download_progress[download_id] = {'progress': 0, 'status': 'pending', 'message': 'Memulai unduhan video...'}

    thread = threading.Thread(target=_download_and_process_single_video, args=(url, format_type, quality, download_id, "Video Unduhan", "N/A", DOWNLOAD_FOLDER, False, 0, 0, app.app_context()))
    thread.start()

    return render_template('download_status.html', download_id=download_id, origin_format_type='video')

@app.route('/download_shorts', methods=['POST'])
def download_shorts_route():
    url = request.form['url']
    download_id = str(uuid.uuid4())
    download_progress[download_id] = {'progress': 0, 'status': 'pending', 'message': 'Memulai unduhan Shorts...'}

    thread = threading.Thread(target=_download_and_process_single_video, args=(url, 'mp4', 'highest', download_id, "Shorts Unduhan", "N/A", DOWNLOAD_FOLDER, False, 0, 0, app.app_context()))
    thread.start()

    return render_template('download_status.html', download_id=download_id, origin_format_type='shorts')

@app.route('/download_playlist', methods=['POST'])
def download_playlist_route():
    playlist_url = request.form['url']
    playlist_download_id = str(uuid.uuid4())
    download_progress[playlist_download_id] = {
        'status': 'pending',
        'message': 'Memuat Playlist...',
        'progress_overall': 0,
        'current_video_progress': 0,
        'video_count': 0,
        'videos_completed': 0,
        'playlist_title': 'Memuat Playlist...',
        'original_url': playlist_url,
        'downloaded_files': []
    }

    thread = threading.Thread(target=perform_download_playlist_task, args=(playlist_url, playlist_download_id, app.app_context()))
    thread.start()

    return render_template('download_status.html', download_id=playlist_download_id, origin_format_type='playlist')


def perform_download_playlist_task(playlist_url, playlist_download_id, app_context):
    with app_context:
        playlist_temp_individual_videos_folder = os.path.join(TEMP_FOLDER, playlist_download_id)
        os.makedirs(playlist_temp_individual_videos_folder, exist_ok=True)
        
        final_zip_filepath = None
        try:
            pl = Playlist(playlist_url)
            download_progress[playlist_download_id]['playlist_title'] = pl.title
            download_progress[playlist_download_id]['video_count'] = len(pl.video_urls)
            download_progress[playlist_download_id]['message'] = f"Playlist '{pl.title}' dimuat. Memulai unduhan..."
            
            downloaded_video_paths_in_playlist = []

            for i, video_url in enumerate(pl.video_urls):
                if not app.debug: 
                    time.sleep(1)
                
                video_title_for_progress = f"Video {i+1}"
                video_thumbnail_for_progress = "N/A"

                video_download_result = _download_and_process_single_video(
                    video_url, 'mp3', 'highest', # Format default MP3, kualitas highest untuk playlist
                    playlist_download_id,
                    video_title_for_progress, 
                    video_thumbnail_for_progress,
                    playlist_temp_individual_videos_folder, 
                    is_playlist_item=True,
                    item_index=i,
                    total_items=len(pl.video_urls),
                    app_context=None # _download_and_process_single_video akan push/pop sendiri jika ini None
                )
                
                if video_download_result['status'] == 'success':
                    downloaded_video_paths_in_playlist.append(video_download_result['filepath'])
                    download_progress[playlist_download_id]['videos_completed'] = i + 1
                    download_progress[playlist_download_id]['progress_overall'] = ((i + 1) / len(pl.video_urls)) * 90 
                    download_progress[playlist_download_id]['message'] = f"Selesai mengunduh ({i+1}/{len(pl.video_urls)}): {video_download_result['title']}"
                else:
                    print(f"Skipping video {video_url} in playlist due to error: {video_download_result['message']}")
                    download_progress[playlist_download_id]['message'] = f"Error video ({i+1}/{len(pl.video_urls)}): {video_download_result['title']} - {video_download_result['message']}"
                
                download_progress[playlist_download_id]['current_video_progress'] = 0

            download_progress[playlist_download_id]['status'] = 'zipping'
            download_progress[playlist_download_id]['message'] = 'Mengompres playlist menjadi ZIP...'
            download_progress[playlist_download_id]['progress_overall'] = 95

            playlist_zip_filename = f"{sanitize_filename(pl.title)}_MP3s.zip"
            final_zip_filepath = os.path.join(PLAYLIST_DOWNLOADS_FOLDER, playlist_zip_filename)

            with zipfile.ZipFile(final_zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for video_path in downloaded_video_paths_in_playlist:
                    if os.path.exists(video_path):
                        zipf.write(video_path, os.path.basename(video_path))
            
            download_progress[playlist_download_id] = {
                'progress': 100,
                'status': 'complete',
                'filepath': final_zip_filepath,
                'download_name': playlist_zip_filename,
                'title': pl.title,
                'thumbnail': pl.videos[0].thumbnail_url if pl.videos else 'N/A',
                'original_url': playlist_url
            }

        except Exception as e:
            error_msg = str(e)
            if "HTTP Error 400" in error_msg or "KeyError" in error_msg:
                error_msg = "Gagal memproses playlist. YouTube mungkin telah melakukan perubahan."
            elif "ffmpeg" in error_msg.lower():
                error_msg = f"Gagal memproses video playlist (ffmpeg error). Pastikan `ffmpeg` terinstal dan MoviePy dapat mengaksesnya."
            else:
                error_msg = f"Terjadi kesalahan tak terduga saat mengunduh playlist: {str(e)}"
            
            download_progress[playlist_download_id] = {
                'progress': 0,
                'status': 'error',
                'message': error_msg,
                'title': locals().get('playlist_title', 'Playlist Unduhan'),
                'thumbnail': locals().get('pl.videos[0].thumbnail_url', 'N/A'),
                'original_url': playlist_url
            }
        finally:
            if os.path.exists(playlist_temp_individual_videos_folder):
                for f in os.listdir(playlist_temp_individual_videos_folder):
                    try:
                        os.remove(os.path.join(playlist_temp_individual_videos_folder, f))
                    except Exception as cleanup_error:
                        print(f"Warning: Failed to clean up residual temp file in playlist folder {f}: {cleanup_error}")
                os.rmdir(playlist_temp_individual_videos_folder)
    
@app.route('/progress/<download_id>')
def progress(download_id):
    status = download_progress.get(download_id, {'progress': 0, 'status': 'not_found'})
    
    if 'video_count' in status and status['video_count'] > 0:
        overall_progress_display = f"{status['videos_completed']}/{status['video_count']} ({status['progress_overall']:.0f}%)"
        if status['status'] == 'downloading':
            status['message'] = status['current_video_message'] if 'current_video_message' in status else "Mengunduh video..."
            status['progress'] = status['current_video_progress']
        elif status['status'] == 'zipping':
            status['message'] = "Mengompres playlist..."
            status['progress'] = status['progress_overall']
        else: # complete, error, pending, not_found
            status['message'] = status['message']
            status['progress'] = status['progress_overall'] if 'progress_overall' in status else status['progress']
        status['display_progress'] = overall_progress_display
    else: # Unduhan video atau shorts biasa
        status['display_progress'] = f"{status['progress']:.0f}%"
    
    return jsonify(status)

@app.route('/serve_download/<download_id>')
def serve_download(download_id):
    download_info = download_progress.get(download_id)
    
    if not download_info or download_info['status'] != 'complete':
        return "Download not complete or not found.", 404
    
    filepath = download_info.get('filepath')
    download_name = download_info.get('download_name')

    if not filepath or not os.path.exists(filepath):
        return "File not found on server.", 404

    file_to_delete = filepath 

    @after_this_request
    def remove_file(response):
        try:
            if os.path.exists(file_to_delete):
                os.remove(file_to_delete)
                print(f"Cleaned up file: {file_to_delete} from DOWNLOAD_FOLDER/PLAYLISTS_FOLDER")
            if download_id in download_progress:
                del download_progress[download_id]
        except Exception as e:
            print(f"Error removing file {file_to_delete}: {e}")
        return response

    mimetype = 'video/mp4' if download_name.endswith('.mp4') else 'audio/mpeg' if download_name.endswith('.mp3') else 'application/octet-stream'
    if download_name.endswith('.zip'):
        mimetype = 'application/zip'

    return send_file(filepath, as_attachment=True, download_name=download_name, mimetype=mimetype)


if __name__ == '__main__':
    app.run(debug=True)