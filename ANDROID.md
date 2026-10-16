# Mr H Media for Android

Android WebView client for https://mr-h-media-web-production.up.railway.app/.

## Build APK

GitHub: Actions > Build Android APK > Run workflow > Download `mr-h-media-android-debug-apk` under Artifacts. Or install Android Studio and open `android/`, then select Build > Build APK(s).

This is a debug APK intended for testing; for public distribution configure your own secure release signing key and build a signed release. User accounts, chat, and uploads remain hosted by Railway, not embedded inside the APK. File selection, basic camera/microphone permissions, and normal browsing are supported. Android System screen capture / `getDisplayMedia` in an Android WebView is not supported by this wrapper and may not work. Some WebRTC configurations require TURN service. Microphone/video access must be explicitly approved by the user. No push-notification service is included; in-site notifications work while the app is running.
