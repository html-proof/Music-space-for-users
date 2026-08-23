package com.musichub.app

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioDeviceCallback
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import androidx.core.app.NotificationCompat

/**
 * Headset Safety Reminder.
 *
 * Runs as a foreground service (not tied to the Flutter UI/Activity
 * lifecycle) so the continuous-connection timer keeps running while the app
 * is backgrounded -- a plain Dart Timer would be suspended or killed by the
 * OS the moment the app leaves the foreground.
 *
 * Uses AudioManager.registerAudioDeviceCallback rather than the legacy
 * ACTION_HEADSET_PLUG broadcast because it covers wired headsets, Bluetooth
 * A2DP, Bluetooth SCO, and USB headsets through one unified callback.
 *
 * The timer measures *continuous* connection time: disconnecting any
 * headset-type device (with none remaining connected) cancels the pending
 * reminder entirely, and a later reconnect starts a fresh hour. While the
 * device stays connected past the first hour, the reminder repeats every
 * additional hour rather than firing once and going silent.
 */
class HeadsetSafetyService : Service() {

    companion object {
        private const val MONITOR_CHANNEL_ID = "headset_safety_monitor"
        private const val REMINDER_CHANNEL_ID = "headset_safety_reminder"
        private const val MONITOR_NOTIFICATION_ID = 4001
        private const val REMINDER_NOTIFICATION_ID = 4002
        private const val CONTINUOUS_LIMIT_MS = 60L * 60L * 1000L // 1 hour

        private val HEADSET_DEVICE_TYPES = mutableSetOf(
            AudioDeviceInfo.TYPE_WIRED_HEADSET,
            AudioDeviceInfo.TYPE_WIRED_HEADPHONES,
            AudioDeviceInfo.TYPE_BLUETOOTH_A2DP,
            AudioDeviceInfo.TYPE_BLUETOOTH_SCO,
        ).apply {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                add(AudioDeviceInfo.TYPE_USB_HEADSET)
            }
        }
    }

    private lateinit var audioManager: AudioManager
    private val handler = Handler(Looper.getMainLooper())
    private var isHeadsetConnected = false

    private val reminderRunnable: Runnable = Runnable {
        postReminderNotification()
        // Safety reminders, not a one-shot: keep nudging every additional
        // hour for as long as the headset stays on.
        handler.postDelayed(reminderRunnable, CONTINUOUS_LIMIT_MS)
    }

    private val audioDeviceCallback = object : AudioDeviceCallback() {
        override fun onAudioDevicesAdded(addedDevices: Array<out AudioDeviceInfo>) {
            refreshConnectionState()
        }

        override fun onAudioDevicesRemoved(removedDevices: Array<out AudioDeviceInfo>) {
            refreshConnectionState()
        }
    }

    override fun onCreate() {
        super.onCreate()
        audioManager = getSystemService(AUDIO_SERVICE) as AudioManager
        createNotificationChannels()
        audioManager.registerAudioDeviceCallback(audioDeviceCallback, handler)
        // Pick up a headset that was already connected before the service started.
        refreshConnectionState()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val notification = buildMonitorNotification()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                MONITOR_NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE,
            )
        } else {
            startForeground(MONITOR_NOTIFICATION_ID, notification)
        }
        return START_STICKY
    }

    override fun onDestroy() {
        audioManager.unregisterAudioDeviceCallback(audioDeviceCallback)
        handler.removeCallbacks(reminderRunnable)
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun refreshConnectionState() {
        val devices = audioManager.getDevices(AudioManager.GET_DEVICES_OUTPUTS)
        val nowConnected = devices.any { it.type in HEADSET_DEVICE_TYPES }

        if (nowConnected == isHeadsetConnected) return
        isHeadsetConnected = nowConnected

        if (nowConnected) {
            // Continuous-connection timer starts now; any previous pending
            // callback (there shouldn't be one, but be defensive) is cleared first.
            handler.removeCallbacks(reminderRunnable)
            handler.postDelayed(reminderRunnable, CONTINUOUS_LIMIT_MS)
        } else {
            // Disconnected -- reset. A later reconnect starts a fresh hour.
            handler.removeCallbacks(reminderRunnable)
        }
    }

    private fun createNotificationChannels() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = getSystemService(NotificationManager::class.java)

        manager.createNotificationChannel(
            NotificationChannel(
                MONITOR_CHANNEL_ID,
                "Headset safety monitoring",
                NotificationManager.IMPORTANCE_MIN,
            ).apply {
                description = "Silent, ongoing notification while headset connection time is tracked."
                setShowBadge(false)
            }
        )

        manager.createNotificationChannel(
            NotificationChannel(
                REMINDER_CHANNEL_ID,
                "Headset safety reminders",
                NotificationManager.IMPORTANCE_DEFAULT,
            ).apply {
                description = "Reminds you to take a break after continuous headset use."
            }
        )
    }

    private fun buildMonitorNotification(): android.app.Notification {
        return NotificationCompat.Builder(this, MONITOR_CHANNEL_ID)
            .setContentTitle("Headset safety reminder active")
            .setSmallIcon(android.R.drawable.ic_lock_silent_mode)
            .setPriority(NotificationCompat.PRIORITY_MIN)
            .setOngoing(true)
            .setSilent(true)
            .build()
    }

    private fun postReminderNotification() {
        val openApp = packageManager.getLaunchIntentForPackage(packageName)
        val pendingIntent = openApp?.let {
            PendingIntent.getActivity(
                this, 0, it,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
        }

        val notification = NotificationCompat.Builder(this, REMINDER_CHANNEL_ID)
            .setContentTitle("Time for a listening break")
            .setContentText("Please remove your headset for 20 minutes.")
            .setSmallIcon(android.R.drawable.ic_popup_reminder)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setAutoCancel(true)
            .apply { pendingIntent?.let { setContentIntent(it) } }
            .build()

        val manager = getSystemService(NotificationManager::class.java)
        manager.notify(REMINDER_NOTIFICATION_ID, notification)
    }
}
