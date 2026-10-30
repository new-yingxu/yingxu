Pod::Spec.new do |s|
  s.name           = 'YingxuMediaAccess'
  s.version        = '1.0.0'
  s.summary        = 'Native folder access and verified media copy for Yingxu Pocket'
  s.description    = 'Provides platform file-picker access, media scanning, verified copy and secure storage.'
  s.license        = { :type => 'UNLICENSED' }
  s.author         = 'Yingxu'
  s.homepage       = 'https://yingxu-app.github.io/smart-media-backup/'
  s.platforms      = { :ios => '15.1' }
  s.swift_version  = '5.9'
  s.source         = { :path => '.' }
  s.static_framework = true
  s.source_files   = 'ios/**/*.{h,m,mm,swift}'
  s.dependency 'ExpoModulesCore'
end
