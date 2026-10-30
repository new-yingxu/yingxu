package expo.modules.yingxumediaaccess

import android.content.ContentResolver
import android.content.Context
import android.content.Intent
import android.media.ExifInterface
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.DocumentsContract
import android.provider.OpenableColumns
import android.util.Base64
import expo.modules.kotlin.Promise
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.OutputStream
import java.security.KeyStore
import java.security.MessageDigest
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import expo.modules.kotlin.activityresult.AppContextActivityResultContract
import expo.modules.kotlin.activityresult.AppContextActivityResultLauncher
import expo.modules.interfaces.permissions.PermissionsStatus

private data class IncomingWrite(
  val output: OutputStream,
  val temporaryFile: File?,
  val temporaryDocument: Uri?,
  val targetFile: File?,
  val targetTree: Uri?,
  val targetName: String,
  val expectedHash: String,
  val size: Long,
  var received: Long = 0,
)

private class OpenDocumentTreeContract : AppContextActivityResultContract<String, String?> {
  override fun createIntent(context: Context, input: String): Intent = Intent(Intent.ACTION_OPEN_DOCUMENT_TREE).apply {
    addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION or Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)
    if (input.isNotBlank() && Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
      putExtra(DocumentsContract.EXTRA_INITIAL_URI, Uri.parse(input))
    }
  }

  override fun parseResult(input: String, resultCode: Int, intent: Intent?): String? =
    if (resultCode == android.app.Activity.RESULT_OK) intent?.data?.toString() else null
}

class YingxuMediaAccessModule : Module() {
  private val incomingWrites = mutableMapOf<String, IncomingWrite>()
  private var directoryLauncher: AppContextActivityResultLauncher<String, String?>? = null
  private var pendingPicker: Promise? = null
  private val photoExtensions = setOf("jpg", "jpeg", "png", "heic", "heif", "webp", "tif", "tiff", "bmp", "gif", "dng", "arw", "cr2", "cr3", "nef", "nrw", "orf", "rw2", "raf", "pef", "srw", "3fr", "ari", "bay", "cap", "data", "dcs", "dcr", "drf", "eip", "erf", "fff", "gpr", "iiq", "k25", "kdc", "mdc", "mef", "mos", "mrw", "obm", "ptx", "pxn", "r3d", "rwl", "rwz", "x3f")
  private val videoExtensions = setOf("mp4", "mov", "m4v", "avi", "mkv", "mts", "m2ts", "3gp", "mpg", "mpeg", "wmv", "webm", "insv", "lrv", "braw", "crm")

  override fun definition() = ModuleDefinition {
    Name("YingxuMediaAccess")

    RegisterActivityContracts {
      directoryLauncher = registerForActivityResult(OpenDocumentTreeContract())
    }

    AsyncFunction("pickDirectoryAsync") { promise: Promise ->
      val launcher = directoryLauncher
      if (launcher == null) promise.reject("PICKER_UNAVAILABLE", "系统文件选择器当前不可用", null)
      else {
        pendingPicker = promise
        launcher.launch("") { result ->
          val pending = pendingPicker
          pendingPicker = null
          if (result == null) {
            pending?.resolve(null)
          } else {
            val uri = Uri.parse(result)
            try {
              appContext.reactContext?.contentResolver?.takePersistableUriPermission(
                uri,
                Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION,
              )
            } catch (_: SecurityException) {}
            pending?.resolve(mapOf("uri" to result, "name" to displayName(uri)))
          }
        }
      }
    }

    AsyncFunction("documentsDirectoryAsync") {
      val context = appContext.reactContext ?: throw IllegalStateException("Android context unavailable")
      val root = context.getExternalFilesDir(Environment.DIRECTORY_DOCUMENTS) ?: context.filesDir
      root.mkdirs()
      Uri.fromFile(root).toString()
    }

    AsyncFunction("startBackupForeground") { title: String, message: String ->
      val context = appContext.reactContext ?: throw IllegalStateException("Android context unavailable")
      val intent = Intent(context, BackupForegroundService::class.java)
        .putExtra("title", title)
        .putExtra("message", message)
      if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) context.startForegroundService(intent)
      else context.startService(intent)
    }

    AsyncFunction("stopBackupForeground") {
      val context = appContext.reactContext ?: throw IllegalStateException("Android context unavailable")
      context.stopService(Intent(context, BackupForegroundService::class.java))
    }

    AsyncFunction("requestNotificationPermissionAsync") { promise: Promise ->
      if (Build.VERSION.SDK_INT < 33) promise.resolve(true)
      else {
        val permissions = appContext.permissions
        if (permissions == null) promise.reject("PERMISSION_UNAVAILABLE", "系统通知权限请求暂不可用", null)
        else if (permissions.hasGrantedPermissions(android.Manifest.permission.POST_NOTIFICATIONS)) promise.resolve(true)
        else {
          permissions.askForPermissions({ result ->
            promise.resolve(result[android.Manifest.permission.POST_NOTIFICATIONS]?.status == PermissionsStatus.GRANTED)
          }, android.Manifest.permission.POST_NOTIFICATIONS)
        }
      }
    }

    AsyncFunction("scanMediaAsync") { root: String -> scanMedia(root) }

    AsyncFunction("readFileChunkBase64Async") { source: String, offset: Double, length: Int ->
      require(length in 1..4096) { "文件分块大小无效" }
      val context = appContext.reactContext ?: throw IllegalStateException("Android context unavailable")
      val input = openInput(context, Uri.parse(source)) ?: error("无法读取来源文件")
      input.use { stream ->
        var remaining = maxOf(0L, offset.toLong())
        while (remaining > 0) {
          val skipped = stream.skip(remaining)
          if (skipped <= 0) break
          remaining -= skipped
        }
        val buffer = ByteArray(length)
        val count = stream.read(buffer)
        mapOf("data" to Base64.encodeToString(if (count > 0) buffer.copyOf(count) else ByteArray(0), Base64.NO_WRAP), "bytesRead" to maxOf(0, count))
      }
    }

    AsyncFunction("readThumbnailBase64Async") { source: String, maxDimension: Int, quality: Int ->
      require(maxDimension in 64..4096) { "缩略图尺寸无效" }
      require(quality in 20..100) { "压缩质量无效" }
      val context = appContext.reactContext ?: throw IllegalStateException("Android context unavailable")
      thumbnailBase64(context, Uri.parse(source), maxDimension, quality)
    }

    AsyncFunction("sha256Async") { source: String -> digest(appContext.reactContext ?: error("Android context unavailable"), Uri.parse(source)) }

    AsyncFunction("copyFileVerifiedAsync") { source: String, destinationRoot: String, relativePath: String ->
      copyVerified(source, destinationRoot, relativePath)
    }

    AsyncFunction("beginIncomingWriteAsync") { destinationRoot: String, relativePath: String, size: Double, expectedHash: String ->
      require(size >= 0 && size <= 20.0 * 1024 * 1024 * 1024 && expectedHash.matches(Regex("^[a-fA-F0-9]{64}$"))) { "电脑文件信息无效" }
      require(!relativePath.startsWith('/') && !relativePath.endsWith('/') && !relativePath.contains("//")) { "目标路径无效" }
      val rawParts = relativePath.split('/')
      require(rawParts.isNotEmpty() && rawParts.size <= 12 && rawParts.all { it.isNotBlank() && it != "." && it != ".." }) { "目标路径无效" }
      val parts = rawParts.map { safeSegment(it) }
      val targetRoot = Uri.parse(destinationRoot)
      val context = appContext.reactContext ?: error("Android context unavailable")
      val resolver = context.contentResolver
      val expected = expectedHash.lowercase()
      val totalSize = size.toLong()
      var targetFile: File? = null
      var targetTree: Uri? = null
      var targetName = parts.last()
      var tempFile: File? = null
      var tempDocument: Uri? = null
      val output: OutputStream

      if (targetRoot.scheme == ContentResolver.SCHEME_FILE) {
        val root = File(targetRoot.path ?: throw IllegalArgumentException("目标目录无效")).canonicalFile
        var parent = root
        for (part in parts.dropLast(1)) parent = File(parent, part)
        parent.mkdirs()
        val canonicalParent = parent.canonicalFile
        require(canonicalParent.path == root.path || canonicalParent.path.startsWith(root.path + File.separator)) { "目标目录越界" }
        val requested = File(canonicalParent, targetName)
        if (requested.exists() && digestFile(requested) == expected) return@AsyncFunction mapOf("status" to "skipped", "sha256" to expected, "size" to totalSize)
        if (requested.exists()) targetName = uniqueConflictName(targetName) { File(canonicalParent, it).exists() }
        targetFile = File(canonicalParent, targetName)
        tempFile = File.createTempFile(".yingxu-", ".part", canonicalParent)
        output = FileOutputStream(tempFile!!)
      } else {
        val treeId = try { DocumentsContract.getTreeDocumentId(targetRoot) } catch (_: Exception) { DocumentsContract.getDocumentId(targetRoot) }
        var parentId = treeId
        for (part in parts.dropLast(1)) {
          var childId = findChildId(resolver, targetRoot, parentId, part)
          if (childId == null) {
            val parentUri = DocumentsContract.buildDocumentUriUsingTree(targetRoot, parentId)
            childId = DocumentsContract.getDocumentId(DocumentsContract.createDocument(resolver, parentUri,
              DocumentsContract.Document.MIME_TYPE_DIR, part) ?: error("无法创建目标文件夹"))
          }
          parentId = childId
        }
        val existingId = findChildId(resolver, targetRoot, parentId, targetName)
        if (existingId != null) {
          val existingUri = DocumentsContract.buildDocumentUriUsingTree(targetRoot, existingId)
          if (digest(context, existingUri) == expected) return@AsyncFunction mapOf("status" to "skipped", "sha256" to expected, "size" to totalSize)
          targetName = uniqueConflictName(targetName) { findChildId(resolver, targetRoot, parentId, it) != null }
        }
        targetTree = DocumentsContract.buildDocumentUriUsingTree(targetRoot, parentId)
        val tempName = ".yingxu-${UUID.randomUUID()}.part"
        tempDocument = DocumentsContract.createDocument(resolver, targetTree!!, "application/octet-stream", tempName)
          ?: error("无法在目标存储创建临时文件")
        output = resolver.openOutputStream(tempDocument!!, "w") ?: run {
          DocumentsContract.deleteDocument(resolver, tempDocument!!)
          error("无法写入目标存储")
        }
      }

      val transferId = UUID.randomUUID().toString()
      incomingWrites[transferId] = IncomingWrite(output, tempFile, tempDocument, targetFile, targetTree,
        targetName, expected, totalSize)
      mapOf("status" to "receiving", "transferId" to transferId)
    }

    AsyncFunction("appendIncomingChunkAsync") { transferId: String, encoded: String ->
      val write = incomingWrites[transferId] ?: error("没有进行中的电脑文件写入")
      val data = Base64.decode(encoded, Base64.DEFAULT)
      require(data.size <= 4096 && write.received + data.size <= write.size) { "电脑文件分块无效" }
      write.output.write(data)
      write.received += data.size
      mapOf("received" to write.received)
    }

    AsyncFunction("finishIncomingWriteAsync") { transferId: String ->
      val write = incomingWrites.remove(transferId) ?: error("没有进行中的电脑文件写入")
      try {
        write.output.flush()
        (write.output as? FileOutputStream)?.fd?.sync()
        write.output.close()
        require(write.received == write.size) { "电脑文件尚未完整接收" }
        val context = appContext.reactContext ?: error("Android context unavailable")
        val tempUri = write.temporaryFile?.let { Uri.fromFile(it) } ?: write.temporaryDocument ?: error("临时文件状态无效")
        require(digest(context, tempUri) == write.expectedHash) { "SHA-256 校验失败，未保留不完整文件" }
        val finalUri: Uri
        if (write.temporaryFile != null && write.targetFile != null) {
          require(!write.targetFile.exists() && write.temporaryFile.renameTo(write.targetFile)) { "无法安全提交目标文件" }
          finalUri = Uri.fromFile(write.targetFile)
        } else {
          val renamed = DocumentsContract.renameDocument(context.contentResolver, write.temporaryDocument!!, write.targetName)
            ?: error("目标存储不支持安全提交文件，请选择其他目标文件夹")
          finalUri = renamed
        }
        if (digest(context, finalUri) != write.expectedHash) {
          if (write.targetFile != null) write.targetFile.delete() else runCatching { DocumentsContract.deleteDocument(context.contentResolver, finalUri) }
          error("目标文件提交后校验失败，已移除不完整文件")
        }
        mapOf("status" to "copied", "sha256" to write.expectedHash, "size" to write.size)
      } catch (cause: Exception) {
        abortIncomingWrite(write)
        throw cause
      }
    }

    AsyncFunction("abortIncomingWriteAsync") { transferId: String ->
      incomingWrites.remove(transferId)?.let { abortIncomingWrite(it) }
    }

    AsyncFunction("secureSet") { key: String, value: String -> secureSet(key, value) }
    AsyncFunction("secureGet") { key: String -> secureGet(key) }
    AsyncFunction("secureRemove") { key: String ->
      val context = appContext.reactContext ?: throw IllegalStateException("Android context unavailable")
      context.getSharedPreferences("yingxu-secure", Context.MODE_PRIVATE).edit().remove(key).apply()
    }
  }

  private fun scanMedia(rawRoot: String): List<Map<String, Any>> {
    val root = Uri.parse(rawRoot)
    if (root.scheme == ContentResolver.SCHEME_FILE) {
      val base = File(root.path ?: throw IllegalArgumentException("目录无效"))
      require(base.isDirectory) { "所选来源无法读取，请重新授权" }
      return base.walkTopDown().filter { it.isFile && isMedia(it.name) }.map { file ->
        mapOf<String, Any>("uri" to Uri.fromFile(file).toString(), "name" to file.name,
          "relativePath" to file.relativeTo(base).invariantSeparatorsPath,
          "size" to file.length(), "modifiedAt" to file.lastModified().toDouble(),
          "capturedAt" to (exifCapturedAt(file) ?: 0.0),
          "mediaType" to mediaType(file.name))
      }.toList()
    }
    val context = appContext.reactContext ?: throw IllegalStateException("Android context unavailable")
    val treeId = try { DocumentsContract.getTreeDocumentId(root) } catch (_: Exception) { DocumentsContract.getDocumentId(root) }
    val found = mutableListOf<Map<String, Any>>()
    walkTree(context.contentResolver, root, treeId, "", found)
    return found
  }

  private fun walkTree(resolver: ContentResolver, tree: Uri, documentId: String, relative: String, out: MutableList<Map<String, Any>>) {
    val children = DocumentsContract.buildChildDocumentsUriUsingTree(tree, documentId)
    val columns = arrayOf(DocumentsContract.Document.COLUMN_DOCUMENT_ID, OpenableColumns.DISPLAY_NAME,
      DocumentsContract.Document.COLUMN_MIME_TYPE, OpenableColumns.SIZE, DocumentsContract.Document.COLUMN_LAST_MODIFIED)
    resolver.query(children, columns, null, null, null)?.use { cursor ->
      val idIndex = cursor.getColumnIndex(DocumentsContract.Document.COLUMN_DOCUMENT_ID)
      val nameIndex = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
      val mimeIndex = cursor.getColumnIndex(DocumentsContract.Document.COLUMN_MIME_TYPE)
      val sizeIndex = cursor.getColumnIndex(OpenableColumns.SIZE)
      val modifiedIndex = cursor.getColumnIndex(DocumentsContract.Document.COLUMN_LAST_MODIFIED)
      while (cursor.moveToNext()) {
        val id = cursor.getString(idIndex) ?: continue
        val name = cursor.getString(nameIndex) ?: ""
        val mime = cursor.getString(mimeIndex) ?: ""
        val childRelative = listOf(relative, name).filter { it.isNotEmpty() }.joinToString("/")
        if (mime == DocumentsContract.Document.MIME_TYPE_DIR) {
          walkTree(resolver, tree, id, childRelative, out)
        } else if (isMedia(name)) {
          val child = DocumentsContract.buildDocumentUriUsingTree(tree, id)
          out.add(mapOf<String, Any>("uri" to child.toString(), "name" to name, "relativePath" to childRelative,
            "size" to maxOf(0L, if (sizeIndex >= 0 && !cursor.isNull(sizeIndex)) cursor.getLong(sizeIndex) else 0L),
            "modifiedAt" to (if (modifiedIndex >= 0 && !cursor.isNull(modifiedIndex)) cursor.getLong(modifiedIndex) else 0L).toDouble(),
            "capturedAt" to (exifCapturedAt(resolver, child) ?: 0.0),
            "mediaType" to mediaType(name)))
        }
      }
    }
  }

  private fun exifCapturedAt(file: File): Double? = try {
    FileInputStream(file).use { input ->
      val exif = ExifInterface(input)
      exifDateToMillis(exif.getAttribute(ExifInterface.TAG_DATETIME_ORIGINAL)
        ?: exif.getAttribute(ExifInterface.TAG_DATETIME_DIGITIZED))
    }
  } catch (_: Exception) { null }

  private fun exifCapturedAt(resolver: ContentResolver, uri: Uri): Double? = try {
    resolver.openInputStream(uri)?.use { input ->
      val exif = ExifInterface(input)
      exifDateToMillis(exif.getAttribute(ExifInterface.TAG_DATETIME_ORIGINAL)
        ?: exif.getAttribute(ExifInterface.TAG_DATETIME_DIGITIZED))
    }
  } catch (_: Exception) { null }

  private fun exifDateToMillis(raw: String?): Double? {
    if (raw.isNullOrBlank()) return null
    return try { SimpleDateFormat("yyyy:MM:dd HH:mm:ss", Locale.US).parse(raw)?.time?.toDouble() } catch (_: Exception) { null }
  }

  private fun copyVerified(source: String, target: String, relativePath: String): Map<String, Any> {
    val context = appContext.reactContext ?: throw IllegalStateException("Android context unavailable")
    val parts = relativePath.split('/').filter { it.isNotBlank() && it != "." && it != ".." }.map { safeSegment(it) }
    require(parts.isNotEmpty()) { "备份路径无效" }
    val fileName = parts.last()
    var parent = Uri.parse(target)
    if (parent.scheme == ContentResolver.SCHEME_FILE) {
      val root = File(parent.path ?: throw IllegalArgumentException("目标目录无效"))
      var output = File(root, parts.joinToString(File.separator))
      output.parentFile?.mkdirs()
      var actualName = fileName
      if (output.exists()) {
        val sourceHash = digest(context, Uri.parse(source))
        if (digestFile(output) == sourceHash) return mapOf("status" to "skipped", "sha256" to sourceHash, "size" to output.length())
        actualName = conflictName(fileName, 2)
        output = File(output.parentFile, actualName)
        var suffix = 3
        while (output.exists()) output = File(output.parentFile, conflictName(fileName, suffix++))
      }
      copyUriToFile(context, Uri.parse(source), output)
      val sourceHash = digest(context, Uri.parse(source))
      val targetHash = digestFile(output)
      if (sourceHash != targetHash) { output.delete(); error("SHA-256 校验失败，目标文件已移除") }
      return mapOf("status" to "copied", "sha256" to targetHash, "size" to output.length())
    }

    val root = Uri.parse(target)
    val treeId = try { DocumentsContract.getTreeDocumentId(root) } catch (_: Exception) { DocumentsContract.getDocumentId(root) }
    var parentId = treeId
    for (part in parts.dropLast(1)) {
      var childId = findChildId(context.contentResolver, root, parentId, part)
      if (childId == null) {
        val parentUri = DocumentsContract.buildDocumentUriUsingTree(root, parentId)
        val created = DocumentsContract.createDocument(context.contentResolver, parentUri,
          DocumentsContract.Document.MIME_TYPE_DIR, part) ?: error("无法创建目标文件夹")
        childId = DocumentsContract.getDocumentId(created)
      }
      parentId = childId
    }
    val parentUri = DocumentsContract.buildDocumentUriUsingTree(root, parentId)
    val existingId = findChildId(context.contentResolver, root, parentId, fileName)
    val sourceUri = Uri.parse(source)
    val sourceHash = digest(context, sourceUri)
    if (existingId != null) {
      val existing = DocumentsContract.buildDocumentUriUsingTree(root, existingId)
      if (digest(context, existing) == sourceHash) return mapOf("status" to "skipped", "sha256" to sourceHash, "size" to querySize(context, sourceUri))
    }
    var outputName = if (existingId == null) fileName else conflictName(fileName, 2)
    var suffix = 3
    while (findChildId(context.contentResolver, root, parentId, outputName) != null) {
      outputName = conflictName(fileName, suffix++)
    }
    val output = DocumentsContract.createDocument(context.contentResolver, parentUri, mimeFor(fileName), outputName)
      ?: error("无法创建目标文件")
    try { copyUriToUri(context, sourceUri, output) } catch (error: Exception) {
      runCatching { DocumentsContract.deleteDocument(context.contentResolver, output) }
      throw error
    }
    val destinationHash = digest(context, output)
    if (sourceHash != destinationHash) {
      DocumentsContract.deleteDocument(context.contentResolver, output)
      error("SHA-256 校验失败，目标文件已移除")
    }
    return mapOf("status" to "copied", "sha256" to destinationHash, "size" to querySize(context, sourceUri))
  }

  private fun findChildId(resolver: ContentResolver, tree: Uri, parentId: String, name: String): String? {
    val children = DocumentsContract.buildChildDocumentsUriUsingTree(tree, parentId)
    resolver.query(children, arrayOf(DocumentsContract.Document.COLUMN_DOCUMENT_ID, OpenableColumns.DISPLAY_NAME), null, null, null)?.use { cursor ->
      val idIndex = cursor.getColumnIndex(DocumentsContract.Document.COLUMN_DOCUMENT_ID)
      val nameIndex = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
      while (cursor.moveToNext()) if (cursor.getString(nameIndex) == name) return cursor.getString(idIndex)
    }
    return null
  }

  private fun copyUriToFile(context: Context, source: Uri, target: File) {
    val input = openInput(context, source) ?: error("无法读取来源文件")
    input.use { stream -> FileOutputStream(target).use { output -> stream.copyTo(output, 1024 * 1024); output.fd.sync() } }
  }

  private fun copyUriToUri(context: Context, source: Uri, target: Uri) {
    val input = openInput(context, source) ?: error("无法读取来源文件")
    val output = context.contentResolver.openOutputStream(target, "w") ?: error("无法写入目标文件")
    input.use { incoming -> output.use { outgoing -> incoming.copyTo(outgoing, 1024 * 1024); outgoing.flush() } }
  }

  private fun openInput(context: Context, uri: Uri) = if (uri.scheme == ContentResolver.SCHEME_FILE) FileInputStream(File(uri.path!!)) else context.contentResolver.openInputStream(uri)

  private fun thumbnailBase64(context: Context, uri: Uri, maxDimension: Int, quality: Int): Map<String, Any> {
    val input = openInput(context, uri) ?: error("无法读取来源文件")
    val bytes = input.use { it.readBytes() }
    val bounds = android.graphics.BitmapFactory.Options().apply { inJustDecodeBounds = true }
    android.graphics.BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
    if (bounds.outWidth <= 0 || bounds.outHeight <= 0) error("不是可解码的图片")
    var sample = 1
    val longestSide = maxOf(bounds.outWidth, bounds.outHeight)
    while (longestSide / (sample * 2) >= maxDimension) sample *= 2
    val options = android.graphics.BitmapFactory.Options().apply { inSampleSize = sample }
    val decoded = android.graphics.BitmapFactory.decodeByteArray(bytes, 0, bytes.size, options) ?: error("图片解码失败")
    val currentLongest = maxOf(decoded.width, decoded.height)
    val scaled = if (currentLongest > maxDimension) {
      val ratio = maxDimension.toFloat() / currentLongest
      android.graphics.Bitmap.createScaledBitmap(decoded, (decoded.width * ratio).toInt().coerceAtLeast(1), (decoded.height * ratio).toInt().coerceAtLeast(1), true)
    } else decoded
    val out = java.io.ByteArrayOutputStream()
    scaled.compress(android.graphics.Bitmap.CompressFormat.JPEG, quality, out)
    val width = scaled.width
    val height = scaled.height
    if (scaled !== decoded) scaled.recycle()
    decoded.recycle()
    return mapOf("data" to Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP), "width" to width, "height" to height, "mimeType" to "image/jpeg")
  }

  private fun digest(context: Context, uri: Uri): String {
    val input = openInput(context, uri) ?: error("无法读取文件进行校验")
    val sha = MessageDigest.getInstance("SHA-256")
    input.use { stream -> val buffer = ByteArray(1024 * 1024); while (true) { val read = stream.read(buffer); if (read < 0) break; sha.update(buffer, 0, read) } }
    return sha.digest().joinToString("") { "%02x".format(it) }
  }

  private fun digestFile(file: File): String {
    val sha = MessageDigest.getInstance("SHA-256")
    FileInputStream(file).use { stream -> val buffer = ByteArray(1024 * 1024); while (true) { val read = stream.read(buffer); if (read < 0) break; sha.update(buffer, 0, read) } }
    return sha.digest().joinToString("") { "%02x".format(it) }
  }

  private fun querySize(context: Context, uri: Uri): Long = try {
    context.contentResolver.query(uri, arrayOf(OpenableColumns.SIZE), null, null, null)?.use { if (it.moveToFirst()) it.getLong(0) else 0L } ?: 0L
  } catch (_: Exception) { 0L }

  private fun displayName(uri: Uri): String = try {
    appContext.reactContext?.contentResolver?.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { if (it.moveToFirst()) it.getString(0) else "外接存储" } ?: "外接存储"
  } catch (_: Exception) { "外接存储" }

  private fun isMedia(name: String): Boolean = mediaType(name).isNotEmpty()
  private fun mediaType(name: String): String {
    val ext = name.substringAfterLast('.', "").lowercase()
    return when { photoExtensions.contains(ext) -> "photo"; videoExtensions.contains(ext) -> "video"; else -> "" }
  }
  private fun safeSegment(value: String) = value.replace(Regex("[<>:\"/\\\\|?*\\u0000-\\u001f]"), "_").trim('.', ' ').take(120).ifBlank { "未命名" }
  private fun uniqueConflictName(name: String, exists: (String) -> Boolean): String {
    val stem = name.substringBeforeLast('.', name)
    val ext = name.substringAfterLast('.', "")
    var suffix = 2
    while (true) {
      val candidate = if (ext.isEmpty()) "$stem ($suffix)" else "$stem ($suffix).$ext"
      if (!exists(candidate)) return candidate
      suffix++
    }
  }

  private fun abortIncomingWrite(write: IncomingWrite) {
    runCatching { write.output.close() }
    write.temporaryFile?.delete()
    write.temporaryDocument?.let { runCatching { DocumentsContract.deleteDocument(appContext.reactContext!!.contentResolver, it) } }
  }
  private fun mimeFor(name: String) = android.webkit.MimeTypeMap.getSingleton().getMimeTypeFromExtension(name.substringAfterLast('.', "").lowercase()) ?: "application/octet-stream"
  private fun conflictName(name: String, number: Int): String { val stem = name.substringBeforeLast('.', name); val ext = name.substringAfterLast('.', ""); return if (ext.isEmpty()) "$stem ($number)" else "$stem ($number).$ext" }

  private fun secureSet(key: String, value: String) {
    val context = appContext.reactContext ?: throw IllegalStateException("Android context unavailable")
    val cipher = Cipher.getInstance("AES/GCM/NoPadding")
    cipher.init(Cipher.ENCRYPT_MODE, secretKey())
    val encrypted = cipher.doFinal(value.toByteArray(Charsets.UTF_8))
    val packed = cipher.iv + encrypted
    context.getSharedPreferences("yingxu-secure", Context.MODE_PRIVATE).edit().putString(key, Base64.encodeToString(packed, Base64.NO_WRAP)).apply()
  }

  private fun secureGet(key: String): String? {
    val context = appContext.reactContext ?: throw IllegalStateException("Android context unavailable")
    val encoded = context.getSharedPreferences("yingxu-secure", Context.MODE_PRIVATE).getString(key, null) ?: return null
    val packed = Base64.decode(encoded, Base64.NO_WRAP)
    val iv = packed.copyOfRange(0, 12)
    val cipher = Cipher.getInstance("AES/GCM/NoPadding")
    cipher.init(Cipher.DECRYPT_MODE, secretKey(), GCMParameterSpec(128, iv))
    return String(cipher.doFinal(packed.copyOfRange(12, packed.size)), Charsets.UTF_8)
  }

  private fun secretKey(): SecretKey {
    val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
    val alias = "yingxu-pocket-firebase"
    val existing = store.getKey(alias, null) as? SecretKey
    if (existing != null) return existing
    val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
    val spec = KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
      .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).setRandomizedEncryptionRequired(true).build()
    generator.init(spec)
    return generator.generateKey()
  }
}
