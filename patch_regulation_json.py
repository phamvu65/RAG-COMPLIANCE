"""
Patch regulation_graph.json: thêm trường linked_activities
vào mỗi Condition để RegulationLoader tạo REQUIRES edges.

Chạy 1 lần:
  cd e:/graph-rag-compliance
  python patch_regulation_json.py

Sau khi patch, chạy lại nb03 để reload regulation graph.
"""
import json
import os
import shutil

JSON_PATH = 'data/raw/sop/regulation_graph.json'

# Backup
backup_path = JSON_PATH + '.bak'
if not os.path.exists(backup_path):
    shutil.copy2(JSON_PATH, backup_path)
    print(f"Đã backup: {backup_path}")

# Load
with open(JSON_PATH, 'r', encoding='utf-8') as f:
    reg = json.load(f)

print(f"Trước patch:")
print(f"  Activities : {len(reg['activities'])}")
print(f"  Sequences  : {len(reg['sequences'])}")
print(f"  Conditions : {len(reg['conditions'])}")
print(f"  Roles      : {len(reg['roles'])}")

# Mapping: article_ref trong Condition → Activity IDs
# Logic: mỗi Condition liên kết với Activity mà checker
#         dùng để kiểm tra ràng buộc đó
ARTICLE_TO_ACTIVITIES = {
    'Article 14(1)': ['returned_offer'],       # O_Returned
    'Article 7(1)' : ['incomplete'],            # A_Incomplete
    'Article 5(1)' : ['sent_mail_online',       # O_Sent (mail and online)
                       'sent_online'],           # O_Sent (online only)
    'Article 8(1)' : ['concept'],               # A_Concept
}

patched = 0
for cond in reg['conditions']:
    art = cond.get('article_ref', '')
    linked = ARTICLE_TO_ACTIVITIES.get(art, [])
    if linked:
        cond['linked_activities'] = linked
        patched += 1
        print(f"  Patched: {cond['id']} ({art}) → {linked}")
    else:
        cond['linked_activities'] = []
        print(f"  No mapping: {cond['id']} ({art})")

# Save
with open(JSON_PATH, 'w', encoding='utf-8') as f:
    json.dump(reg, f, ensure_ascii=False, indent=2)

print(f"\nĐã patch {patched} conditions")
print(f"Đã lưu: {JSON_PATH}")
print(f"\nBước tiếp theo:")
print(f"  1. Chạy lại notebook 03 để reload regulation graph")
print(f"  2. Hoặc chạy fix_requires_edges.py nếu chỉ muốn thêm edges")
print(f"  3. Verify: MATCH ()-[r:REQUIRES]->() RETURN count(r)")