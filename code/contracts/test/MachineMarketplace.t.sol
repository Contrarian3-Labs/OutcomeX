// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {MachineAssetNFT, TransferGuardBlocked} from "../src/MachineAssetNFT.sol";
import {MachineMarketplace} from "../src/MachineMarketplace.sol";
import {OrderBook} from "../src/OrderBook.sol";
import {PWRToken} from "../src/PWRToken.sol";
import {RevenueVault} from "../src/RevenueVault.sol";
import {SettlementController} from "../src/SettlementController.sol";
import {MockUSDCWithAuthorization} from "../src/mocks/MockUSDCWithAuthorization.sol";
import {MockUSDT} from "../src/mocks/MockUSDT.sol";
import {TestBase} from "./utils/TestBase.sol";

contract MachineMarketplaceTest is TestBase {
    address internal constant ADMIN = address(0xA11CE);
    address internal constant PLATFORM_TREASURY = address(0xBEEF);
    address internal constant PAYMENT_ADAPTER = address(0xAD7E2);
    address internal constant SELLER = address(0xCAFE);
    address internal constant BUYER = address(0xB0B);
    address internal constant BUYER_TWO = address(0xB0C);

    uint64 internal constant LISTING_EXPIRY = 30 days;

    MockUSDCWithAuthorization internal usdc;
    MockUSDT internal usdt;
    PWRToken internal pwr;
    MachineAssetNFT internal machineAsset;
    RevenueVault internal revenueVault;
    SettlementController internal settlement;
    OrderBook internal orderBook;
    MachineMarketplace internal marketplace;

    uint256 internal machineId;

    function setUp() public {
        usdc = new MockUSDCWithAuthorization();
        usdt = new MockUSDT();
        pwr = new PWRToken(ADMIN);
        machineAsset = new MachineAssetNFT(ADMIN);
        revenueVault = new RevenueVault(ADMIN, address(pwr), address(machineAsset), 25);
        settlement = new SettlementController(ADMIN, address(revenueVault), address(pwr), PLATFORM_TREASURY);
        orderBook = new OrderBook(ADMIN, address(machineAsset));

        address[] memory supportedTokens = new address[](2);
        supportedTokens[0] = address(usdc);
        supportedTokens[1] = address(usdt);
        marketplace = new MachineMarketplace(ADMIN, address(machineAsset), supportedTokens);

        vm.startPrank(ADMIN);
        pwr.setMinter(address(revenueVault), true);
        revenueVault.setSettlementController(address(settlement));
        settlement.setOrderBook(address(orderBook));
        orderBook.setSettlementController(address(settlement));
        orderBook.setRevenueVault(address(revenueVault));
        orderBook.setPaymentAdapter(PAYMENT_ADAPTER);
        machineAsset.setTransferGuard(address(orderBook));
        machineId = machineAsset.mintMachine(SELLER, "ipfs://machine-001");
        usdc.mint(BUYER, 5_000_000);
        usdt.mint(BUYER, 5_000_000);
        vm.stopPrank();
    }

    function testCreateListingAndCancel() public {
        vm.prank(SELLER);
        machineAsset.approve(address(marketplace), machineId);

        vm.prank(SELLER);
        uint256 listingId =
            marketplace.createListing(machineId, address(usdc), 1_000_000, uint64(block.timestamp + LISTING_EXPIRY));

        (
            uint256 storedId,
            uint256 storedMachineId,
            address seller,
            address paymentToken,
            uint256 price,
            uint64 expiry,
            bool active
        ) = marketplace.getListing(listingId);

        assertEq(storedId, listingId, "listing id mismatch");
        assertEq(storedMachineId, machineId, "machine id mismatch");
        assertEq(seller, SELLER, "seller mismatch");
        assertEq(paymentToken, address(usdc), "payment token mismatch");
        assertEq(price, 1_000_000, "price mismatch");
        assertEq(expiry, uint64(block.timestamp + LISTING_EXPIRY), "expiry mismatch");
        assertTrue(active, "listing should be active");
        assertEq(marketplace.activeListingIdByMachine(machineId), listingId, "active listing index mismatch");

        vm.prank(SELLER);
        marketplace.cancelListing(listingId);

        (, , , , , , active) = marketplace.getListing(listingId);
        assertTrue(!active, "listing should be inactive after cancellation");
        assertEq(marketplace.activeListingIdByMachine(machineId), 0, "active listing index should clear");
    }

    function testBuyListingTransfersNftAndPaysSeller() public {
        vm.prank(SELLER);
        machineAsset.approve(address(marketplace), machineId);

        vm.prank(SELLER);
        uint256 listingId =
            marketplace.createListing(machineId, address(usdt), 1_250_000, uint64(block.timestamp + LISTING_EXPIRY));

        vm.prank(BUYER);
        usdt.approve(address(marketplace), 1_250_000);

        uint256 sellerBalanceBefore = usdt.balanceOf(SELLER);
        vm.prank(BUYER);
        marketplace.buyListing(listingId);

        assertEq(machineAsset.ownerOf(machineId), BUYER, "buyer should receive NFT");
        assertEq(usdt.balanceOf(SELLER), sellerBalanceBefore + 1_250_000, "seller should receive proceeds");
        assertEq(usdt.balanceOf(BUYER), 5_000_000 - 1_250_000, "buyer should pay price");
        assertEq(marketplace.activeListingIdByMachine(machineId), 0, "active listing index should clear after purchase");
    }

    function testCreateListingBlockedWhenMachineHasActiveTask() public {
        vm.prank(BUYER_TWO);
        uint256 orderId = orderBook.createOrder(machineId, 500);

        vm.prank(PAYMENT_ADAPTER);
        orderBook.markOrderPaid(orderId, true, true, address(usdc));

        vm.prank(SELLER);
        machineAsset.approve(address(marketplace), machineId);

        vm.startPrank(SELLER);
        vm.expectRevert(abi.encodeWithSelector(TransferGuardBlocked.selector, machineId, orderBook.REASON_ACTIVE_TASK()));
        marketplace.createListing(machineId, address(usdc), 1_000_000, uint64(block.timestamp + LISTING_EXPIRY));
        vm.stopPrank();
    }

    function testBuyListingBlockedWhenMachineBecomesGuardedAfterListing() public {
        vm.prank(SELLER);
        machineAsset.approve(address(marketplace), machineId);

        vm.prank(SELLER);
        uint256 listingId =
            marketplace.createListing(machineId, address(usdc), 1_000_000, uint64(block.timestamp + LISTING_EXPIRY));

        vm.prank(BUYER_TWO);
        uint256 orderId = orderBook.createOrder(machineId, 500);

        vm.prank(PAYMENT_ADAPTER);
        orderBook.markOrderPaid(orderId, true, true, address(usdc));

        vm.prank(BUYER);
        usdc.approve(address(marketplace), 1_000_000);

        vm.startPrank(BUYER);
        vm.expectRevert(abi.encodeWithSelector(TransferGuardBlocked.selector, machineId, orderBook.REASON_ACTIVE_TASK()));
        marketplace.buyListing(listingId);
        vm.stopPrank();
    }

    function testCannotBuyCancelledOrExpiredListing() public {
        vm.prank(SELLER);
        machineAsset.approve(address(marketplace), machineId);

        vm.prank(SELLER);
        uint256 cancelledListingId =
            marketplace.createListing(machineId, address(usdc), 1_000_000, uint64(block.timestamp + LISTING_EXPIRY));

        vm.prank(SELLER);
        marketplace.cancelListing(cancelledListingId);

        vm.prank(BUYER);
        usdc.approve(address(marketplace), 1_000_000);

        vm.startPrank(BUYER);
        vm.expectRevert(bytes("LISTING_INACTIVE"));
        marketplace.buyListing(cancelledListingId);
        vm.stopPrank();

        vm.prank(SELLER);
        uint256 expiredListingId =
            marketplace.createListing(machineId, address(usdc), 1_000_000, uint64(block.timestamp + 1));

        vm.warp(block.timestamp + 2);

        vm.startPrank(BUYER);
        vm.expectRevert(bytes("LISTING_EXPIRED"));
        marketplace.buyListing(expiredListingId);
        vm.stopPrank();
    }
}
