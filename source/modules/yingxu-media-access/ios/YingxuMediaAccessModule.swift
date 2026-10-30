import ExpoModulesCore
import CryptoKit
import Foundation
import Security
import UIKit
import UniformTypeIdentifiers
import ImageIO

private final class YingxuDocumentPickerDelegate: NSObject, UIDocumentPickerDelegate {
  let didPick: ([URL]) -> Void
  let didCancel: () -> Void

  init(didPick: @escaping ([URL]) -> Void, didCancel: @escaping () -> Void) {
    self.didPick = didPick
    self.didCancel = didCancel
  }

  func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) {
    didPick(urls)
  }

  func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) {
    didCancel()
  }
}

public final class YingxuMediaAccessModule: Module {
  private struct IncomingWrite {
    let handle: FileHandle
    let temporary: URL
    let target: URL
    let expectedHash: String
    let size: Int64
    var received: Int64
  }

  private var pickerPromise: Promise?
  private var activeURLs: [String: URL] = [:]
  private var incomingWrites: [String: IncomingWrite] = [:]
  private let service = "com.yingxuapp.pocket.secure"
  private lazy var pickerDelegate = YingxuDocumentPickerDelegate(
    didPick: { [weak self] urls in self?.finishPickerSelection(urls) },
    didCancel: { [weak self] in self?.finishPickerCancellation() }
  )
  private let photoExtensions: Set<String> = [
    "jpg", "jpeg", "png", "heic", "heif", "webp", "tif", "tiff", "bmp", "gif",
    "dng", "arw", "cr2", "cr3", "nef", "nrw", "orf", "rw2", "raf", "pef", "srw",
    "3fr", "ari", "bay", "cap", "data", "dcs", "dcr", "drf", "eip", "erf", "fff",
    "gpr", "iiq", "k25", "kdc", "mdc", "mef", "mos", "mrw", "obm", "ptx", "pxn",
    "r3d", "rwl", "rwz", "x3f"
  ]
  private let videoExtensions: Set<String> = [
    "mp4", "mov", "m4v", "avi", "mkv", "mts", "m2ts", "3gp", "mpg", "mpeg", "wmv",
    "webm", "insv", "lrv", "braw", "crm"
  ]

  public func definition() -> ModuleDefinition {
    Name("YingxuMediaAccess")

    AsyncFunction("pickDirectoryAsync") { (promise: Promise) in
      DispatchQueue.main.async {
        guard let presenter = self.topViewController() else {
          promise.reject("PICKER_UNAVAILABLE", "系统文件选择器当前不可用")
          return
        }
        self.pickerPromise = promise
        let picker = UIDocumentPickerViewController(forOpeningContentTypes: [.folder], asCopy: false)
        picker.delegate = self.pickerDelegate
        picker.allowsMultipleSelection = false
        presenter.present(picker, animated: true)
      }
    }

    AsyncFunction("documentsDirectoryAsync") { () -> String in
      FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].absoluteString
    }

    AsyncFunction("scanMediaAsync") { (root: String) throws -> [[String: Any]] in
      let rootURL = try self.accessibleURL(root)
      let keys: [URLResourceKey] = [.isDirectoryKey, .fileSizeKey, .contentModificationDateKey]
      guard let enumerator = FileManager.default.enumerator(at: rootURL, includingPropertiesForKeys: keys,
                                                           options: [.skipsHiddenFiles]) else {
        throw NSError(domain: "YingxuMediaAccess", code: 1,
                      userInfo: [NSLocalizedDescriptionKey: "无法读取所选文件夹"])
      }
      var results: [[String: Any]] = []
      for case let url as URL in enumerator {
        let values = try? url.resourceValues(forKeys: Set(keys))
        if values?.isDirectory == true { continue }
        let ext = url.pathExtension.lowercased()
        let mediaType = self.photoExtensions.contains(ext) ? "photo" :
          (self.videoExtensions.contains(ext) ? "video" : "")
        if mediaType.isEmpty { continue }
        let relativePath = String(url.path.dropFirst(rootURL.path.count)).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        results.append([
          "uri": url.absoluteString,
          "name": url.lastPathComponent,
          "relativePath": relativePath,
          "size": values?.fileSize ?? 0,
          "modifiedAt": (values?.contentModificationDate?.timeIntervalSince1970 ?? 0) * 1000,
          "capturedAt": self.exifCapturedAt(url) ?? NSNull(),
          "mediaType": mediaType
        ])
      }
      return results
    }

    AsyncFunction("readFileChunkBase64Async") { (source: String, offset: Double, length: Int) throws -> [String: Any] in
      guard length > 0 && length <= 4096 else {
        throw NSError(domain: "YingxuMediaAccess", code: 5,
                      userInfo: [NSLocalizedDescriptionKey: "文件分块大小无效"])
      }
      let url = try self.accessibleURL(source)
      let handle = try FileHandle(forReadingFrom: url)
      defer { try? handle.close() }
      try handle.seek(toOffset: UInt64(max(0, offset)))
      let data = try handle.read(upToCount: length) ?? Data()
      return ["data": data.base64EncodedString(), "bytesRead": data.count]
    }

    AsyncFunction("readThumbnailBase64Async") { (source: String, maxDimension: Int, quality: Int) throws -> [String: Any] in
      guard (64...4096).contains(maxDimension), (20...100).contains(quality) else {
        throw NSError(domain: "YingxuMediaAccess", code: 7, userInfo: [NSLocalizedDescriptionKey: "缩略图参数无效"])
      }
      let url = try self.accessibleURL(source)
      return try self.thumbnailBase64(url: url, maxDimension: maxDimension, quality: quality)
    }

    AsyncFunction("sha256Async") { (source: String) throws -> String in
      try self.sha256(self.accessibleURL(source))
    }

    AsyncFunction("copyFileVerifiedAsync") { (source: String, destinationRoot: String, relativePath: String) throws -> [String: Any] in
      let sourceURL = try self.accessibleURL(source)
      let rootURL = try self.accessibleURL(destinationRoot)
      let segments = relativePath.split(separator: "/").map { self.safeSegment(String($0)) }
      guard let originalName = segments.last, !segments.isEmpty else {
        throw NSError(domain: "YingxuMediaAccess", code: 2,
                      userInfo: [NSLocalizedDescriptionKey: "备份路径无效"])
      }
      var parent = rootURL
      for segment in segments.dropLast() {
        parent.appendPathComponent(segment, isDirectory: true)
        try FileManager.default.createDirectory(at: parent, withIntermediateDirectories: true)
      }
      var targetURL = parent.appendingPathComponent(originalName)
      if FileManager.default.fileExists(atPath: targetURL.path) {
        let sourceHash = try self.sha256(sourceURL)
        let existingHash = try self.sha256(targetURL)
        if sourceHash == existingHash {
          return ["status": "skipped", "sha256": sourceHash, "size": (try sourceURL.resourceValues(forKeys: [.fileSizeKey])).fileSize ?? 0]
        }
        let stem = targetURL.deletingPathExtension().lastPathComponent
        let ext = targetURL.pathExtension
        var suffix = 2
        repeat {
          targetURL = parent.appendingPathComponent("\(stem) (\(suffix)).\(ext)")
          suffix += 1
        } while FileManager.default.fileExists(atPath: targetURL.path)
      }
      try FileManager.default.copyItem(at: sourceURL, to: targetURL)
      let sourceHash = try self.sha256(sourceURL)
      let destinationHash = try self.sha256(targetURL)
      guard sourceHash == destinationHash else {
        try? FileManager.default.removeItem(at: targetURL)
        throw NSError(domain: "YingxuMediaAccess", code: 3,
                      userInfo: [NSLocalizedDescriptionKey: "SHA-256 校验失败，目标文件已移除"])
      }
      return ["status": "copied", "sha256": destinationHash,
              "size": (try sourceURL.resourceValues(forKeys: [.fileSizeKey])).fileSize ?? 0]
    }

    AsyncFunction("beginIncomingWriteAsync") { (destinationRoot: String, relativePath: String, size: Double, expectedHash: String) throws -> [String: Any] in
      guard size >= 0, size <= Double(20 * 1024 * 1024 * 1024), expectedHash.range(of: "^[a-fA-F0-9]{64}$", options: .regularExpression) != nil else {
        throw NSError(domain: "YingxuMediaAccess", code: 6, userInfo: [NSLocalizedDescriptionKey: "电脑文件信息无效"])
      }
      let rawSegments = relativePath.split(separator: "/", omittingEmptySubsequences: false).map(String.init)
      guard !rawSegments.isEmpty, rawSegments.count <= 12, rawSegments.allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." }) else {
        throw NSError(domain: "YingxuMediaAccess", code: 7, userInfo: [NSLocalizedDescriptionKey: "目标路径无效"])
      }
      let rootURL = try self.accessibleURL(destinationRoot).standardizedFileURL
      let segments = rawSegments.map(self.safeSegment)
      var parent = rootURL
      for segment in segments.dropLast() {
        parent.appendPathComponent(segment, isDirectory: true)
        try FileManager.default.createDirectory(at: parent, withIntermediateDirectories: true)
      }
      let rootPrefix = rootURL.path.hasSuffix("/") ? rootURL.path : rootURL.path + "/"
      guard parent.standardizedFileURL.path == rootURL.path || parent.standardizedFileURL.path.hasPrefix(rootPrefix) else {
        throw NSError(domain: "YingxuMediaAccess", code: 7, userInfo: [NSLocalizedDescriptionKey: "目标目录越界"])
      }
      let originalName = segments.last!
      var target = parent.appendingPathComponent(originalName)
      if FileManager.default.fileExists(atPath: target.path), try self.sha256(target) == expectedHash.lowercased() {
        return ["status": "skipped", "sha256": expectedHash.lowercased(), "size": Int64(size)]
      }
      if FileManager.default.fileExists(atPath: target.path) {
        let stem = target.deletingPathExtension().lastPathComponent
        let ext = target.pathExtension
        var suffix = 2
        repeat {
          let name = ext.isEmpty ? "\(stem) (\(suffix))" : "\(stem) (\(suffix)).\(ext)"
          target = parent.appendingPathComponent(name)
          suffix += 1
        } while FileManager.default.fileExists(atPath: target.path)
      }
      let temporary = parent.appendingPathComponent(".yingxu-\(UUID().uuidString).part")
      guard FileManager.default.createFile(atPath: temporary.path, contents: nil) else {
        throw NSError(domain: "YingxuMediaAccess", code: 8, userInfo: [NSLocalizedDescriptionKey: "无法创建临时文件，请检查目标存储空间和权限"])
      }
      let handle = try FileHandle(forWritingTo: temporary)
      let transferID = UUID().uuidString
      self.incomingWrites[transferID] = IncomingWrite(handle: handle, temporary: temporary, target: target,
                                                       expectedHash: expectedHash.lowercased(), size: Int64(size), received: 0)
      return ["status": "receiving", "transferId": transferID]
    }

    AsyncFunction("appendIncomingChunkAsync") { (transferID: String, encoded: String) throws -> [String: Any] in
      guard var write = self.incomingWrites[transferID], let data = Data(base64Encoded: encoded), data.count <= 4096,
            write.received + Int64(data.count) <= write.size else {
        throw NSError(domain: "YingxuMediaAccess", code: 9, userInfo: [NSLocalizedDescriptionKey: "电脑文件分块无效"])
      }
      try write.handle.write(contentsOf: data)
      write.received += Int64(data.count)
      self.incomingWrites[transferID] = write
      return ["received": write.received]
    }

    AsyncFunction("finishIncomingWriteAsync") { (transferID: String) throws -> [String: Any] in
      guard let write = self.incomingWrites.removeValue(forKey: transferID) else {
        throw NSError(domain: "YingxuMediaAccess", code: 10, userInfo: [NSLocalizedDescriptionKey: "没有进行中的电脑文件写入"])
      }
      do {
        try write.handle.synchronize()
        try write.handle.close()
        guard write.received == write.size, try self.sha256(write.temporary) == write.expectedHash else {
          throw NSError(domain: "YingxuMediaAccess", code: 11, userInfo: [NSLocalizedDescriptionKey: "文件未完整接收或 SHA-256 校验失败"])
        }
        guard !FileManager.default.fileExists(atPath: write.target.path) else {
          throw NSError(domain: "YingxuMediaAccess", code: 12, userInfo: [NSLocalizedDescriptionKey: "目标文件刚刚发生变化，请重试"])
        }
        try FileManager.default.moveItem(at: write.temporary, to: write.target)
        return ["status": "copied", "sha256": write.expectedHash, "size": write.size]
      } catch {
        try? FileManager.default.removeItem(at: write.temporary)
        throw error
      }
    }

    AsyncFunction("abortIncomingWriteAsync") { (transferID: String) in
      guard let write = self.incomingWrites.removeValue(forKey: transferID) else { return }
      try? write.handle.close()
      try? FileManager.default.removeItem(at: write.temporary)
    }

    AsyncFunction("secureSet") { (key: String, value: String) throws in
      let account = self.accountName(key)
      let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                  kSecAttrService as String: self.service,
                                  kSecAttrAccount as String: account]
      let data = Data(value.utf8)
      let status = SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
      if status == errSecItemNotFound {
        var item = query
        item[kSecValueData as String] = data
        let addStatus = SecItemAdd(item as CFDictionary, nil)
        guard addStatus == errSecSuccess else { throw self.keychainError(addStatus) }
      } else if status != errSecSuccess {
        throw self.keychainError(status)
      }
    }

    AsyncFunction("secureGet") { (key: String) throws -> String? in
      var query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                  kSecAttrService as String: self.service,
                                  kSecAttrAccount as String: self.accountName(key),
                                  kSecReturnData as String: true,
                                  kSecMatchLimit as String: kSecMatchLimitOne]
      query[kSecUseDataProtectionKeychain as String] = true
      var result: CFTypeRef?
      let status = SecItemCopyMatching(query as CFDictionary, &result)
      if status == errSecItemNotFound { return nil }
      guard status == errSecSuccess, let data = result as? Data else { throw self.keychainError(status) }
      return String(data: data, encoding: .utf8)
    }

    AsyncFunction("secureRemove") { (key: String) throws in
      let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                  kSecAttrService as String: self.service,
                                  kSecAttrAccount as String: self.accountName(key)]
      let status = SecItemDelete(query as CFDictionary)
      guard status == errSecSuccess || status == errSecItemNotFound else { throw self.keychainError(status) }
    }
  }

  private func finishPickerSelection(_ urls: [URL]) {
    guard let url = urls.first, let promise = pickerPromise else { return }
    pickerPromise = nil
    _ = url.startAccessingSecurityScopedResource()
    activeURLs[url.absoluteString] = url
    if let bookmark = try? url.bookmarkData(options: [], includingResourceValuesForKeys: nil, relativeTo: nil) {
      UserDefaults.standard.set(bookmark, forKey: bookmarkKey(url.absoluteString))
    }
    promise.resolve(["uri": url.absoluteString, "name": url.lastPathComponent])
  }

  private func finishPickerCancellation() {
    pickerPromise?.resolve(nil)
    pickerPromise = nil
  }

  private func topViewController() -> UIViewController? {
    let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
    guard let root = scenes.flatMap({ $0.windows }).first(where: { $0.isKeyWindow })?.rootViewController else { return nil }
    var current = root
    while let presented = current.presentedViewController { current = presented }
    return current
  }

  private func thumbnailBase64(url: URL, maxDimension: Int, quality: Int) throws -> [String: Any] {
    guard let imageSource = CGImageSourceCreateWithURL(url as CFURL, nil) else {
      throw NSError(domain: "YingxuMediaAccess", code: 6, userInfo: [NSLocalizedDescriptionKey: "无法读取图片"])
    }
    let options: [CFString: Any] = [
      kCGImageSourceCreateThumbnailFromImageAlways: true,
      kCGImageSourceCreateThumbnailWithTransform: true,
      kCGImageSourceShouldCacheImmediately: true,
      kCGImageSourceThumbnailMaxPixelSize: maxDimension,
    ]
    guard let cgImage = CGImageSourceCreateThumbnailAtIndex(imageSource, 0, options as CFDictionary) else {
      throw NSError(domain: "YingxuMediaAccess", code: 6, userInfo: [NSLocalizedDescriptionKey: "图片解码失败"])
    }
    let image = UIImage(cgImage: cgImage)
    guard let data = image.jpegData(compressionQuality: CGFloat(quality) / 100.0) else {
      throw NSError(domain: "YingxuMediaAccess", code: 6, userInfo: [NSLocalizedDescriptionKey: "图片编码失败"])
    }
    return ["data": data.base64EncodedString(), "width": cgImage.width, "height": cgImage.height, "mimeType": "image/jpeg"]
  }

  private func accessibleURL(_ raw: String) throws -> URL {
    guard let url = URL(string: raw) else {
      throw NSError(domain: "YingxuMediaAccess", code: 4,
                    userInfo: [NSLocalizedDescriptionKey: "文件夹授权已失效，请重新选择"])
    }
    if let active = activeURLs[raw] { return active }
    guard let bookmark = UserDefaults.standard.data(forKey: bookmarkKey(raw)) else { return url }
    var stale = false
    let resolved = try URL(resolvingBookmarkData: bookmark, options: [.withoutUI], relativeTo: nil, bookmarkDataIsStale: &stale)
    if stale, let updated = try? resolved.bookmarkData(options: [], includingResourceValuesForKeys: nil, relativeTo: nil) {
      UserDefaults.standard.set(updated, forKey: bookmarkKey(raw))
    }
    _ = resolved.startAccessingSecurityScopedResource()
    activeURLs[raw] = resolved
    return resolved
  }

  private func bookmarkKey(_ value: String) -> String {
    "yingxu.folder." + SHA256.hash(data: Data(value.utf8)).map { String(format: "%02x", $0) }.joined()
  }

  private func exifCapturedAt(_ url: URL) -> Double? {
    guard let source = CGImageSourceCreateWithURL(url as CFURL, [kCGImageSourceShouldCache: false] as CFDictionary),
          let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
          let exif = properties[kCGImagePropertyExifDictionary] as? [CFString: Any],
          let raw = (exif[kCGImagePropertyExifDateTimeOriginal] as? String) ?? (exif[kCGImagePropertyExifDateTimeDigitized] as? String)
    else { return nil }
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.timeZone = .current
    formatter.dateFormat = "yyyy:MM:dd HH:mm:ss"
    return formatter.date(from: raw).map { $0.timeIntervalSince1970 * 1000 }
  }

  private func safeSegment(_ raw: String) -> String {
    let forbidden = CharacterSet(charactersIn: "/\\:\u{0}")
    let value = raw.components(separatedBy: forbidden).filter { !$0.isEmpty }.joined(separator: "_")
      .trimmingCharacters(in: CharacterSet(charactersIn: ". "))
    return value.isEmpty || value == ".." ? "未命名" : String(value.prefix(120))
  }

  private func sha256(_ url: URL) throws -> String {
    let handle = try FileHandle(forReadingFrom: url)
    defer { try? handle.close() }
    var hasher = SHA256()
    while true {
      let data = try handle.read(upToCount: 1024 * 1024) ?? Data()
      if data.isEmpty { break }
      hasher.update(data: data)
    }
    return hasher.finalize().map { String(format: "%02x", $0) }.joined()
  }

  private func accountName(_ raw: String) -> String {
    SHA256.hash(data: Data(raw.utf8)).map { String(format: "%02x", $0) }.joined()
  }

  private func keychainError(_ status: OSStatus) -> NSError {
    NSError(domain: "YingxuSecureStore", code: Int(status),
            userInfo: [NSLocalizedDescriptionKey: "系统安全存储操作失败（\(status)）"])
  }
}
