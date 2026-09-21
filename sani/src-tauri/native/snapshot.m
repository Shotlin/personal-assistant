// Sani's own rendered webview, captured to a PNG.
//
// `screencapture` needs macOS Screen Recording permission, which a headless
// verification session does not have; an app may always render its own
// WKWebView, so this gives real pixels of the pill/panel for UI checks without
// any extra TCC grant.

#import <AppKit/AppKit.h>
#import <WebKit/WebKit.h>
#include <stdlib.h>

static void write_png(NSImage *image, NSString *path) {
  if (image == nil) {
    return;
  }
  NSData *tiff = [image TIFFRepresentation];
  NSBitmapImageRep *rep = [NSBitmapImageRep imageRepWithData:tiff];
  NSData *png = [rep representationUsingType:NSBitmapImageFileTypePNG
                                  properties:@{}];
  if (png != nil) {
    [png writeToFile:path atomically:YES];
  }
}

void sani_webview_snapshot(void *webview_ptr, const char *path_utf8) {
  if (webview_ptr == NULL || path_utf8 == NULL) {
    return;
  }
  NSString *path = [NSString stringWithUTF8String:path_utf8];
  void (^work)(void) = ^{
    WKWebView *webview = (__bridge WKWebView *)webview_ptr;
    // Completion is delivered asynchronously on the main queue: nothing here
    // blocks the event loop, which would deadlock the caller.
    [webview takeSnapshotWithConfiguration:nil
                         completionHandler:^(NSImage *image, NSError *error) {
                           (void)error;
                           write_png(image, path);
                         }];
  };
  if ([NSThread isMainThread]) {
    work();
  } else {
    dispatch_async(dispatch_get_main_queue(), work);
  }
}
