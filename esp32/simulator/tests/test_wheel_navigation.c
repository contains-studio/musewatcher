/* Production LVGL UI, simulated camera/network services and wheel events. */
#define main simulator_cli_main
#include "../src/main.c"
#undef main
#include <assert.h>
#include "boards/watcher_camera.h"
#include "muse_wheel_gesture.h"
static void step(unsigned ms) {
    for (unsigned t=0;t<ms;t+=5) { sim_time_advance_us(5000); lv_timer_handler(); }
}
static bool has_text(lv_obj_t *o,const char *text) {
    if(!lv_obj_is_visible(o)) return false;
    if(lv_obj_check_type(o,&lv_label_class) && strstr(lv_label_get_text(o),text)) return true;
    for(uint32_t i=0;i<lv_obj_get_child_count(o);i++) if(has_text(lv_obj_get_child(o,i),text)) return true;
    return false;
}
static void expect(const char *s) { if(!has_text(lv_screen_active(),s)) {fprintf(stderr,"Missing visible text: %s\n",s);abort();} }
static const char *output_dir;
static void snap(const char *name) {
    char p[512];snprintf(p,sizeof(p),"%s/%s.ppm",output_dir,name);assert(write_snapshot(p));
}
static void turn(int d) {muse_ui_wheel_turn(d);step(180);}
static void click(void) {muse_ui_wheel_click();step(180);}
static void card(uint16_t color,bool success) {
    uint16_t row[412];for(int x=0;x<412;x++) row[x]=(color>>8)|(color<<8);
    for(int y=0;y<412;y++) assert(muse_ui_card_draw(0,y,412,1,row));
    muse_ui_card_finish(success);step(200);
}
static const char *reply="Start here. The wheel lets you read this answer at your own pace. Turn forward to move to the next page and turn back to reread something. It will stay on your chosen page even after Muse finishes responding. Press the wheel when you are done reading. You can open the last reply again by turning the wheel while Muse is resting.";
int main(int argc, char **argv) {
    assert(argc == 2); output_dir = argv[1];
    setbuf(stdout,NULL);setenv("SDL_VIDEODRIVER","dummy",1);setenv("SDL_AUDIODRIVER","dummy",1);
    sim_time_reset();sim_services_reset();lv_init();muse_state_init();muse_state_set_power(&s_power);muse_board=sim_board_get();assert(muse_ui_start()==ESP_OK);
    muse_state_set_mode(MUSE_MODE_IDLE);muse_state_set_caption("%s", "");step(800);snap("01-idle");
    muse_ui_reply_update("reply-1",reply);muse_state_set_mode(MUSE_MODE_SPEAKING);muse_state_set_caption("Start here.");step(200);
    turn(1);expect("Page 2/");snap("02-reading-page-2");
    muse_state_set_caption("Automatically advanced caption");step(500);expect("Page 2/");
    muse_state_set_mode(MUSE_MODE_IDLE);muse_state_set_caption("%s", "");step(500);expect("Page 2/");
    turn(-1);expect("Page 1/");expect("Start here.");snap("03-reading-page-1");
    click();assert(!has_text(lv_screen_active(),"Page 1/"));
    turn(1);expect("Last reply");snap("04-reply-browser");click();expect("Page 1/");click();
    puts("PASS manual forward/back pages, no automatic jump, survives reply completion, recalls last answer");
    card(0xf800,true);snap("05-saved-card");muse_ui_image_hide();step(200);
    sim_services_set_camera("live");step(200);card(0x07e0,false);snap("06-camera-with-failed-card");
    sim_services_set_camera("closed");step(200);turn(1);expect("Latest card");snap("07-card-browser");click();snap("08-recalled-card");
    puts("PASS card recall after camera and failed replacement (pixel comparison follows)");
    muse_ui_image_hide();sim_services_set_camera("review");step(200);
    turn(1);expect("Press wheel: Send photo");assert(watcher_camera_state()==WATCHER_CAMERA_REVIEW);snap("09-photo-send-selected");
    turn(1);expect("Press wheel: Retake");snap("10-photo-retake-selected");click();assert(watcher_camera_state()==WATCHER_CAMERA_LIVE);
    turn(1);expect("Press wheel: Take photo");click();assert(watcher_camera_state()==WATCHER_CAMERA_REVIEW);
    turn(-1);expect("Press wheel: Cancel");snap("11-photo-cancel-selected");click();assert(watcher_camera_state()==WATCHER_CAMERA_CLOSED);
    sim_services_set_camera("review");step(200);turn(1);click();assert(watcher_camera_state()==WATCHER_CAMERA_SENDING);
    puts("PASS photo rotation never sends; clicks confirm Retake, Take photo, Cancel, and simulated Send");
    sim_services_set_camera("review");step(200);turn(-1);expect("Press wheel: Cancel");
    muse_wheel_gesture_t gesture = {0};
    muse_wheel_step(&gesture,MUSE_WHEEL_DOWN,10,true);
    muse_wheel_step(&gesture,MUSE_WHEEL_UP,60,true);
    /* Input queues rotation, then passes the same edge to the recognizer. */
    muse_ui_wheel_turn(1);
    unsigned action=muse_wheel_step(&gesture,MUSE_WHEEL_TURN,160,true);
    if(action&MUSE_WHEEL_TAP) muse_ui_wheel_click();
    step(200);expect("Press wheel: Send photo");
    action=muse_wheel_step(&gesture,0,420,true);
    if(action&MUSE_WHEEL_TAP) muse_ui_wheel_click();
    step(200);assert(watcher_camera_state()==WATCHER_CAMERA_REVIEW);
    puts("PASS gesture-to-UI: click Cancel then rotate to Send does not upload");
    sim_services_set_camera("live");step(200);card(0x001f,true);assert(watcher_camera_state()==WATCHER_CAMERA_LIVE);snap("12-camera-during-new-card");
    sim_services_set_camera("closed");step(200);snap("13-deferred-card");
    muse_ui_image_hide();step(200);turn(1);step(10500);assert(!has_text(lv_screen_active(),"Press wheel to open"));
    puts("PASS completed card waits for camera close, browse hint times out");
    sim_services_set_camera("live");step(200);card(0x07e0,true);
    muse_ui_show_animation();sim_services_set_camera("closed");step(200);
    snap("14-cancelled-deferred-card");
    turn(1);expect("Latest card");click();snap("15-recall-cancelled-card");
    puts("PASS show_animation cancels deferred presentation but retains recall");
    lv_deinit();
    return 0;
}
