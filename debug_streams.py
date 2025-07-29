from pytubefix import YouTube

# Ganti dengan URL video yang sedang Anda uji di browser
url_to_test = "https://www.youtube.com/watch?v=yMatGl4PySY&list=RDyMatGl4PySY&start_radio=1" 

try:
    yt = YouTube(url_to_test)
    print(f"Judul Video: {yt.title}")
    print(f"URL Thumbnail: {yt.thumbnail_url}")

    print("\n--- Progressive MP4 Streams (video + audio) ---")
    progressive_mp4_streams = yt.streams.filter(progressive=True, file_extension='mp4').order_by('resolution').desc()

    if progressive_mp4_streams:
        for stream in progressive_mp4_streams:
            print(f"  Tag: {stream.itag}, Resolusi: {stream.resolution}, FPS: {stream.fps}, MimeType: {stream.mime_type}, ABR: {stream.abr}")
    else:
        print("  Tidak ada stream MP4 progresif yang ditemukan untuk video ini.")

    print("\n--- All MP4 Streams (progressive & adaptive) ---")
    all_mp4_streams = yt.streams.filter(file_extension='mp4').order_by('resolution').desc()
    if all_mp4_streams:
        for stream in all_mp4_streams:
            print(f"  Tag: {stream.itag}, Resolusi: {stream.resolution}, FPS: {stream.fps}, MimeType: {stream.mime_type}, Type: {stream.type}, ABR: {stream.abr}, Progressive: {stream.is_progressive}")
    else:
        print("  Tidak ada stream MP4 sama sekali.")

    print("\n--- All Streams (including webm, audio only, etc.) ---")
    all_streams = yt.streams.order_by('resolution').desc()
    if all_streams:
        for stream in all_streams:
            print(f"  Tag: {stream.itag}, Resolusi: {stream.resolution}, FPS: {stream.fps}, MimeType: {stream.mime_type}, Type: {stream.type}, ABR: {stream.abr}, Progressive: {stream.is_progressive}")
    else:
        print("  Tidak ada stream sama sekali.")


except Exception as e:
    print(f"Terjadi error saat mengambil informasi stream: {e}")