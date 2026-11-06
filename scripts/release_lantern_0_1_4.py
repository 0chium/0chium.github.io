from pathlib import Path
import hashlib, gzip, email.utils, os, re, subprocess, tempfile, json

OLD = '0.1.3-1'
NEW = '0.1.4-1'
PKG = 'com.ochium.lantern'
OLD_DEP = 'Depends: mobilesubstrate, com.opa334.ccsupport'
NEW_DEP = OLD_DEP + ', cy+model.iphone (<< 14.7)'
OLD_FN = f'debs/{PKG}_{OLD}_iphoneos-arm64.deb'
NEW_FN = f'debs/{PKG}_{NEW}_iphoneos-arm64.deb'
EXPECTED_SHA256 = '652a6598e9cdb417e88f87219d2efb94d2e9b1a838a265074942a862c269c0fc'
CHANGELOG = ('Installation is now restricted to iPhones with warm/white flashlight LED hardware '
             '(iPhone 13 series and earlier, including SE 3). No changes to flashlight functionality.')

def replace_once(source, old, new):
    count = source.count(old)
    if count != 1:
        raise ValueError(f'Expected exactly one match for {old[:65]!r}, found {count}')
    return source.replace(old, new, 1)

def sh(*cmd):
    return subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True).strip()

def hash_of(data, name):
    return hashlib.new(name, data).hexdigest()

def payload_manifest(root):
    result = {}
    for p in sorted(root.rglob('*')):
        rel = p.relative_to(root)
        if 'DEBIAN' in rel.parts:
            continue
        key = str(rel)
        if p.is_symlink():
            result[key] = ('link', os.readlink(p))
        elif p.is_file():
            result[key] = ('file', hash_of(p.read_bytes(), 'sha256'), p.stat().st_mode & 0o7777)
        elif p.is_dir():
            result[key] = ('dir', p.stat().st_mode & 0o7777)
        else:
            raise ValueError(f'Unexpected payload type: {key}')
    return result

old_deb, new_deb = Path(OLD_FN), Path(NEW_FN)
assert old_deb.is_file() and not new_deb.exists()
assert hash_of(old_deb.read_bytes(), 'sha256') == EXPECTED_SHA256, 'Existing package changed'
assert sh('dpkg-deb', '-f', str(old_deb), 'Package') == PKG
assert sh('dpkg-deb', '-f', str(old_deb), 'Version') == OLD
assert sh('dpkg-deb', '-f', str(old_deb), 'Depends') == OLD_DEP.partition(': ')[2]

with tempfile.TemporaryDirectory() as tmp:
    unpack = Path(tmp) / 'unpack'
    verify = Path(tmp) / 'verify'
    subprocess.run(['dpkg-deb', '-R', str(old_deb), str(unpack)], check=True)
    control = unpack / 'DEBIAN/control'
    original = control.read_text()
    modified = replace_once(original, f'Version: {OLD}\n', f'Version: {NEW}\n')
    modified = replace_once(modified, OLD_DEP + '\n', NEW_DEP + '\n')
    control.write_text(modified)
    subprocess.run(['dpkg-deb', '--build', '--root-owner-group', str(unpack), str(new_deb)], check=True)
    subprocess.run(['dpkg-deb', '-R', str(new_deb), str(verify)], check=True)
    assert payload_manifest(unpack) == payload_manifest(verify), 'Installed payload changed'
    assert sh('dpkg-deb', '-f', str(new_deb), 'Version') == NEW
    assert sh('dpkg-deb', '-f', str(new_deb), 'Depends') == NEW_DEP.partition(': ')[2]

# Debian version comparison, not a string comparison or an iOS-version restriction.
for model, allowed in [('12.5', True), ('12.8', True), ('13.4', True),
                       ('14.5', True), ('14.6', True), ('14.7', False),
                       ('14.8', False), ('15.2', False), ('16.1', False)]:
    got = subprocess.run(['dpkg', '--compare-versions', model, 'lt', '14.7']).returncode == 0
    assert got == allowed, f'Wrong model boundary: {model}'

index_path = Path('Packages')
index = index_path.read_text()
matches = [p for p in index.split('\n\n')
           if p.startswith(f'Package: {PKG}\n') and f'Version: {OLD}\n' in p + '\n']
assert len(matches) == 1, 'Expected one Lantern 0.1.3-1 stanza'
old_stanza = matches[0]
new_stanza = old_stanza
new_stanza = replace_once(new_stanza, f'Version: {OLD}\n', f'Version: {NEW}\n')
new_stanza = replace_once(new_stanza, OLD_DEP + '\n', NEW_DEP + '\n')
new_stanza = replace_once(new_stanza, f'Filename: {OLD_FN}\n', f'Filename: {NEW_FN}\n')
deb_bytes = new_deb.read_bytes()
for key, value in [('Size', str(len(deb_bytes))),
                   ('MD5sum', hash_of(deb_bytes, 'md5')),
                   ('SHA1', hash_of(deb_bytes, 'sha1')),
                   ('SHA256', hash_of(deb_bytes, 'sha256')),
                   ('SHA512', hash_of(deb_bytes, 'sha512'))]:
    new_stanza, changed = re.subn(rf'(?m)^{key}: [^\n]+$', f'{key}: {value}', new_stanza)
    assert changed == 1, f'Invalid index field: {key}'
index = replace_once(index, old_stanza, new_stanza + '\n\n' + old_stanza)
index_path.write_text(index)
gz = gzip.compress(index.encode(), compresslevel=9, mtime=0)
Path('Packages.gz').write_bytes(gz)

release_path = Path('Release')
release = release_path.read_text()
assert release.count('MD5Sum:\n') == 1 and release.count('SHA1:\n') == 1
assert release.count('SHA256:\n') == 1 and release.count('SHA512:\n') == 1
header = release.split('MD5Sum:\n')[0]
header, changed = re.subn(r'(?m)^Date: .*$', f'Date: {email.utils.formatdate(usegmt=True)}',
                          header, count=1)
assert changed == 1
sections = []
for field, algorithm in [('MD5Sum', 'md5'), ('SHA1', 'sha1'),
                         ('SHA256', 'sha256'), ('SHA512', 'sha512')]:
    lines = [field + ':']
    for name, data in [('Packages', index.encode()), ('Packages.gz', gz)]:
        lines.append(f' {hash_of(data, algorithm)} {len(data)} {name}')
    sections.append('\n'.join(lines) + '\n')
release_path.write_text(header + ''.join(sections))

html_path = Path('depiction.html')
html = html_path.read_text()
html = replace_once(html, f'Version {OLD} · Free', f'Version {NEW} · Free')
html = replace_once(html, f"What's new in {OLD}", f"What's new in {NEW}")
html = replace_once(html,
    '<p>Version-only metadata update from 0.1.2-1. All installed binaries and resources are byte-identical. No functional code, LED mapping, injection, dependency or activation changes were made.</p>',
    f'<p>{CHANGELOG}</p>')
html = replace_once(html, f'Version {OLD} preserves those installed binaries and resources.',
                    f'Version {NEW} preserves those installed binaries and resources.')
html_path.write_text(html)

json_path = Path('depictions/lantern.json')
description = json_path.read_text()
description = replace_once(description, f'"title": "Version {OLD}"', f'"title": "Version {NEW}"')
description = replace_once(description, f'"text": "{OLD}"', f'"text": "{NEW}"')
description = replace_once(description,
    'Version-only metadata update from 0.1.2-1. All installed binaries and resources are byte-identical. No functional code, hooks, LED mapping, injection filters, dependencies or activation behavior changed.',
    CHANGELOG)
description = replace_once(description, 'Version 0.1.3-1 has identical installed binaries/resources.',
                           f'Version {NEW} preserves identical installed binaries/resources.')
json_path.write_text(description)

home_path = Path('index.html')
home = home_path.read_text()
home = replace_once(home, f'<span class="tag">{OLD}</span>',
                    f'<span class="tag">{NEW}</span>')
home_path.write_text(home)

assert json.loads(description)['class'] == 'DepictionTabView'
assert len([p for p in index.split('\n\n')
            if p.startswith(f'Package: {PKG}\n') and f'Version: {NEW}\n' in p + '\n']) == 1
assert gzip.decompress(gz).decode() == index
assert 'cy+model.iphone (<< 14.7)' in new_stanza
assert 'cy+model.iphone (<< 14.7)' not in old_stanza
print(f'Validated {PKG} {NEW}: SHA256 {hash_of(deb_bytes,"sha256")}; '
      f'package bytes {len(deb_bytes)}; index bytes {len(index)}')
