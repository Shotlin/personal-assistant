#import <AppKit/AppKit.h>
#include <stdlib.h>
#include <string.h>

// Return AppKit's visibleFrame for every screen. It is the macOS source of
// truth for the menu bar/notch and a Dock placed at the bottom, left, or
// right. Rust copies and frees the returned JSON through the paired function.
const char *sani_visible_work_areas_json(void) {
    @autoreleasepool {
        NSMutableArray *screens = [NSMutableArray array];
        for (NSScreen *screen in NSScreen.screens) {
            NSRect frame = screen.frame;
            NSRect visible = screen.visibleFrame;
            // AppKit no longer exports NSScreenNumber as a public constant,
            // but the stable device-description key remains available.
            NSNumber *number = screen.deviceDescription[@"NSScreenNumber"];
            NSString *name = screen.localizedName ?: @"Display";
            NSString *identifier = [NSString stringWithFormat:@"%@:%@", number ?: @0, name];
            [screens addObject:@{
                @"id": identifier,
                @"screen_x": @(frame.origin.x),
                @"screen_y": @(frame.origin.y),
                @"screen_width": @(frame.size.width),
                @"screen_height": @(frame.size.height),
                @"visible_x": @(visible.origin.x),
                @"visible_y": @(visible.origin.y),
                @"visible_width": @(visible.size.width),
                @"visible_height": @(visible.size.height),
                @"backing_scale_factor": @(screen.backingScaleFactor),
            }];
        }
        NSError *error = nil;
        NSData *data = [NSJSONSerialization dataWithJSONObject:screens options:0 error:&error];
        if (!data || error) {
            return NULL;
        }
        size_t length = data.length;
        char *result = malloc(length + 1);
        if (!result) {
            return NULL;
        }
        memcpy(result, data.bytes, length);
        result[length] = '\0';
        return result;
    }
}

void sani_visible_work_areas_free(const char *json) {
    free((void *)json);
}
