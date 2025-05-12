// Copyright (c) 2018, Frappe and contributors
// For license information, please see license.txt

frappe.ui.form.on('S3 File Attachment', {
    refresh: function(frm) {
        // Subscribe to real-time progress updates
        frappe.realtime.on('progress', (data) => {
            if (data.progress) {
                // Show or update the progress bar with file count
                let message = `Migrating Files: ${data.current_file} of ${data.total_files}`;
                
                if (!frm.progress_bar) {
                    frm.progress_bar = frm.dashboard.show_progress(message, data.progress);
                } else {
                    frm.dashboard.update_progress(message, data.progress);
                }

                
                if (data.progress >= 100) {
                    setTimeout(() => {
                        frm.reload_doc(); 
                    }, 1000);
                }
            }
        });
    },
    
    migrate_existing_files: function(frm) {
        frappe.call({
            method: "frappe_s3_attachment.controller.migrate_existing_files",
            callback: function (data) {
                frappe.msgprint(data.message)
            }
        });
    },
});
