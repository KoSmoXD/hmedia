package com.mrh.media;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.webkit.CookieManager;
import android.webkit.PermissionRequest;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;
import android.view.View;
import java.util.ArrayList;
import java.util.List;

public final class MainActivity extends Activity {
    private static final String HOME = "https://mr-h-media-web-production.up.railway.app/";
    private static final int FILE_PICKER_REQUEST = 1001;
    private static final int MEDIA_PERMISSION_REQUEST = 1002;
    private WebView webView;
    private ValueCallback<Uri[]> fileCallback;
    private PermissionRequest pendingMediaRequest;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        webView = new WebView(this);
        setContentView(webView);
        webView.setBackgroundColor(0xff0c0d14);
        webView.getSettings().setJavaScriptEnabled(true);
        webView.getSettings().setDomStorageEnabled(true);
        webView.getSettings().setMediaPlaybackRequiresUserGesture(false);
        webView.getSettings().setAllowFileAccess(false);
        webView.getSettings().setAllowContentAccess(true);
        webView.getSettings().setJavaScriptCanOpenWindowsAutomatically(false);
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, false);
        webView.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if ("https".equals(uri.getScheme()) && "mr-h-media-web-production.up.railway.app".equals(uri.getHost())) return false;
                try { startActivity(new Intent(Intent.ACTION_VIEW, uri)); } catch (Exception ignored) { Toast.makeText(MainActivity.this, "Cannot open link", Toast.LENGTH_SHORT).show(); }
                return true;
            }
        });
        webView.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                try { startActivityForResult(params.createIntent(), FILE_PICKER_REQUEST); }
                catch (Exception e) { fileCallback = null; callback.onReceiveValue(null); return false; }
                return true;
            }
            @Override public void onPermissionRequest(PermissionRequest request) {
                runOnUiThread(() -> handleMediaRequest(request));
            }
            @Override public void onPermissionRequestCanceled(PermissionRequest request) {
                runOnUiThread(() -> { if (pendingMediaRequest == request) pendingMediaRequest = null; });
            }
        });
        webView.loadUrl(HOME);
    }

    private void handleMediaRequest(PermissionRequest request) {
        if (!"https".equals(request.getOrigin().getScheme()) || !"mr-h-media-web-production.up.railway.app".equals(request.getOrigin().getHost())) { request.deny(); return; }
        List<String> requested = new ArrayList<>();
        List<String> platform = new ArrayList<>();
        for (String resource : request.getResources()) {
            if (PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(resource)) { requested.add(resource); platform.add(Manifest.permission.RECORD_AUDIO); }
            else if (PermissionRequest.RESOURCE_VIDEO_CAPTURE.equals(resource)) { requested.add(resource); platform.add(Manifest.permission.CAMERA); }
        }
        if (requested.isEmpty()) { request.deny(); return; }
        boolean missing = false;
        for (String permission : platform) if (checkSelfPermission(permission) != PackageManager.PERMISSION_GRANTED) missing = true;
        pendingMediaRequest = request;
        if (missing) requestPermissions(platform.toArray(new String[0]), MEDIA_PERMISSION_REQUEST);
        else grantMediaRequest();
    }
    private void grantMediaRequest() {
        PermissionRequest request = pendingMediaRequest;
        pendingMediaRequest = null;
        if (request == null) return;
        List<String> granted = new ArrayList<>();
        for (String resource : request.getResources()) {
            if (PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(resource) && checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) granted.add(resource);
            if (PermissionRequest.RESOURCE_VIDEO_CAPTURE.equals(resource) && checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) granted.add(resource);
        }
        if (granted.isEmpty()) request.deny(); else request.grant(granted.toArray(new String[0]));
    }
    @Override public void onRequestPermissionsResult(int code, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(code, permissions, results);
        if (code == MEDIA_PERMISSION_REQUEST) grantMediaRequest();
    }
    @Override protected void onActivityResult(int code, int result, Intent data) {
        super.onActivityResult(code, result, data);
        if (code == FILE_PICKER_REQUEST && fileCallback != null) {
            Uri[] uris = result == RESULT_OK ? WebChromeClient.FileChooserParams.parseResult(result, data) : null;
            fileCallback.onReceiveValue(uris);
            fileCallback = null;
        }
    }
    @Override public void onBackPressed() {
        if (webView.canGoBack()) webView.goBack(); else super.onBackPressed();
    }
    @Override protected void onDestroy() {
        if (pendingMediaRequest != null) { pendingMediaRequest.deny(); pendingMediaRequest = null; }
        if (fileCallback != null) { fileCallback.onReceiveValue(null); fileCallback = null; }
        webView.destroy();
        super.onDestroy();
    }
}
